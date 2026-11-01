"""Surgical editor for the ``models:`` section of ``config.yaml``.

``config.yaml`` is ~80% comments that document *why* each option is set the way
it is. ``yaml.safe_load`` + ``yaml.dump`` round-tripping throws all of that away,
so this module never reserializes the file. It treats the config as text and
rewrites only the lines inside a delimited "managed region":

    models:
      # >>> DeerFlow Web UI 托管区域 — 界面会整体重写，请勿手工编辑 >>>
      - name: doubao-seed-1.8
        ...
      # <<< DeerFlow Web UI 托管区域结束 <<<
      # Example: Volcengine (Doubao) model
      # - name: doubao-seed-1.8
      #   ...

Every line outside the two marker comments stays byte-identical, including the
commented-out example templates that live under ``models:`` as reference
material for operators. When the markers are absent the region is inserted
directly below the ``models:`` line, above those templates.

Only stdlib + PyYAML are used (the harness layer must stay importable on its own).

Byte-identity guarantee
-----------------------
Everything outside the managed region — comments, blank lines, sections after
``models:``, line endings — is copied through untouched. There are exactly two
documented exceptions, both confined to the ``models:`` key line itself:

1. An inline value on the key line (``models: []``, ``models: null``) is
   discarded, because a value cannot coexist with the block sequence the region
   introduces — ``models: []`` followed by ``  - name: x`` is invalid YAML. Any
   trailing comment is preserved (``models: []  # hi`` → ``models:  # hi``).
2. When the file's final line is an unterminated ``models:`` (no trailing
   newline), a newline is appended so the inserted region cannot merge into the
   key.

Every other input shape is preserved verbatim.

The read/write protocol built on top of it
------------------------------------------
Editing ``config.yaml`` from the web UI is a five-step protocol. Each step is
only allowed to touch the original once the previous one has succeeded::

    1. work = copy of config.yaml        (``config.yaml.work``) — original untouched
    2. rewrite the managed region in the copy    (``replace_managed_section``)
    3. validate the copy                         (``validate_candidate_text``)
         └─ fail -> delete the copy and abort; the original was never touched
    4. backup = ``config.yaml.bak.<timestamp>``  (``commit_config_update``)
         └─ fail -> abort; the original was never touched
    5. ``os.replace(work, config.yaml)``         — atomic, no intermediate state

Validation only catches *static* problems — YAML syntax, an unresolvable
``$VAR``, a bad model entry. It cannot catch a provider field that only blows up
on a real request. That is why the backup is written **before** the atomic
replace instead of after it: without it, a latent runtime problem would leave
the original already overwritten and unrecoverable. Backups are timestamped
rather than a fixed ``config.yaml.bak`` so that a later successful edit cannot
clobber the pristine copy the user needs to roll back to; ``prune_backups``
keeps the most recent N.

No failure path may modify ``config_path``. ``commit_config_update`` reads the
original into memory first, writes the backup, and only then stages the work
file — and if staging or the final ``os.replace`` fails it removes both the work
file and the backup it just wrote, so the directory is left exactly as it was.

Writing a brand-new credential
------------------------------
A model entry may point at a ``$VAR`` that does not exist yet, so saving it has to
produce three things: the ``.env`` entry, the process environment, and the
``config.yaml`` reference. ``load_dotenv()`` runs once, at import
(``app_config.py:44``), which means a key appended to ``.env`` *afterwards* is
invisible to the running process — ``$VAR`` resolves to ``None``,
``resolve_env_variables`` raises, and since the reload path has no exception guard
the next request fails. Writing the file alone is therefore never enough;
``upsert_env_var`` also assigns ``os.environ[key]``.

The order of the whole operation is fixed by a second constraint: validation runs
*before* the config is swapped in, so a key written after validation can never be
referenced by the candidate that is being validated::

    1. upsert_env_var(.env, key, value)   — the file first, then os.environ
    2. surgical replace on a copy
    3. validate the copy                  — $NEW_KEY now resolves
    4. backup
    5. atomic replace

The cost of that order is a residue: if steps 2–5 fail, ``.env`` keeps the new key
while ``config.yaml`` is unchanged. That is harmless — an unreferenced environment
variable changes nothing about how the app runs — and it is deliberately **not**
rolled back. Rewriting ``.env`` to undo it would race with, and could clobber, the
other values the operator typed by hand; a stray unused key is far cheaper than a
lost credential. Tests pin both the residue and the ordering.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.reflection import resolve_class

logger = logging.getLogger(__name__)

MANAGED_BEGIN = "# >>> DeerFlow Web UI 托管区域 — 界面会整体重写，请勿手工编辑 >>>"
MANAGED_END = "# <<< DeerFlow Web UI 托管区域结束 <<<"

#: A top-level mapping key: starts at column 0 with a letter or underscore.
_TOP_LEVEL_KEY_RE = re.compile(r"[a-zA-Z_]")

_MODELS_KEY_RE = re.compile(r"models:")

#: A dotenv variable name: letters, digits and underscores, not starting with a digit.
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_INDENT = "  "

#: Fields a UI payload must carry for `ModelConfig` to accept the entry.
REQUIRED_MODEL_FIELDS = ("use", "model")

#: How many timestamped backups `prune_backups` keeps by default.
DEFAULT_BACKUP_KEEP = 10

#: `config.yaml.bak.20260912-143000`, with `-N` appended for same-second commits.
_BACKUP_STAMP_RE = re.compile(r"(\d{8}-\d{6})(?:-(\d+))?")

_BACKUP_STAMP_FORMAT = "%Y%m%d-%H%M%S"

#: Suffix of the staging copy the write protocol validates before replacing the
#: live file. Lives next to the original so ``os.replace`` stays on one filesystem.
WORK_SUFFIX = ".work"

#: Placeholder shown where a real credential used to be.
_MASK = "****"

#: Shortest secret that can reveal anything while still hiding its core. Below
#: this, the whole value is masked — a 2-character placeholder has no middle to
#: hide and would otherwise leak one of its two characters.
_MASK_REVEAL_MIN_LENGTH = 8

_MASK_PREFIX_LENGTH = 3
_MASK_SUFFIX_LENGTH = 4


def find_models_block(lines: list[str]) -> tuple[int, int] | None:
    """Return the ``[start, end)`` line range of the top-level ``models:`` section.

    ``start`` is the index of the ``models:`` line; ``end`` is the index of the
    next top-level key (a line matching ``^[a-zA-Z_]``) or ``len(lines)`` when no
    top-level key follows. Indented ``models:`` occurrences (e.g. nested under an
    agent) are ignored. Returns ``None`` when the file has no ``models:`` section.
    """
    start: int | None = None
    for index, line in enumerate(lines):
        if _MODELS_KEY_RE.match(line):
            start = index
            break

    if start is None:
        return None

    for index in range(start + 1, len(lines)):
        if _TOP_LEVEL_KEY_RE.match(lines[index]):
            return (start, index)

    return (start, len(lines))


def render_managed_section(models: list[dict]) -> list[str]:
    """Render *models* into managed-region lines, markers included.

    Lines are 2-space indented to match ``config.example.yaml`` style. An empty
    *models* list still yields both markers, so the region stays locatable.
    """
    lines = [MANAGED_BEGIN + "\n"]

    if models:
        body = yaml.safe_dump(
            models,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            indent=2,
        )
        lines.extend(_INDENT + entry + "\n" for entry in body.splitlines())

    lines.append(MANAGED_END + "\n")
    return lines


def replace_managed_section(text: str, models: list[dict]) -> str:
    """Rewrite the managed region of *text* to describe *models*.

    When the markers are present only the lines between them are replaced. When
    they are absent the region is inserted immediately after the ``models:``
    line, and the section's existing *entries* are removed — the region is the
    section's value now, so leaving them behind would put two sequences under
    one key. Comment-only lines (the ``config.example.yaml`` templates) are kept
    where they are, since a comment contributes no value and dropping them would
    throw away the operator's own notes.

    What this discards is exactly what ``load_managed_models`` reports — see
    :func:`_models_section_value_lines`. The two must stay complementary: a save
    writes only the entries the caller submitted, so any entry a load withholds
    is an entry the next save deletes.

    Everything else is preserved verbatim, including line endings. Raises
    ``ValueError`` when the file has no ``models:`` section or carries only one
    of the two markers.
    """
    lines = text.splitlines(keepends=True)
    newline = _detect_newline(lines)
    rendered = _apply_newline(render_managed_section(models), newline)

    begin = _find_marker(lines, MANAGED_BEGIN)
    end = _find_marker(lines, MANAGED_END)
    block = find_models_block(lines)

    if begin is None and end is None:
        if block is None:
            raise ValueError("config text has no top-level 'models:' section to anchor the managed region to")
        anchor = block[0] + 1
        lines[block[0]] = _bare_models_key_line(lines[block[0]], newline)
        kept = [line for line in lines[anchor : block[1]] if _is_comment_or_blank(line)]
        return "".join(lines[:anchor] + rendered + kept + lines[block[1] :])

    if begin is None or end is None or end < begin:
        raise ValueError(f"config text has unbalanced managed-region markers ({MANAGED_BEGIN!r} / {MANAGED_END!r})")

    if block is not None and block[0] < begin:
        # Same inline-value hazard on the already-marked path: `models: []` above
        # an existing region is just as unparseable as one above an inserted one.
        lines[block[0]] = _bare_models_key_line(lines[block[0]], newline)

    return "".join(lines[:begin] + rendered + lines[end + 1 :])


def _is_comment_or_blank(line: str) -> bool:
    """True when *line* carries no YAML value of its own.

    Used by the insertion path to decide what survives from a section it is
    about to take over: comments and blank lines do, everything else is the old
    value and goes.
    """
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _models_section_value_lines(lines: list[str], block: tuple[int, int]) -> list[str]:
    """The lines of a *marker-less* ``models:`` section that hold its value.

    Deliberately the exact complement of what :func:`replace_managed_section`
    keeps when it takes that section over, so reading and writing agree on what
    the section is worth. The agreement is load-bearing, not stylistic: the save
    endpoints write back only the entries the UI submitted, so an entry the read
    path withholds is an entry the next save silently deletes. Both sides test
    the same predicate, which is what makes them complementary rather than two
    rules that happen to match today.
    """
    return [line for line in lines[block[0] + 1 : block[1]] if not _is_comment_or_blank(line)]


def _models_key_inline_value(line: str) -> str | None:
    """The inline value on a ``models:`` key line, or ``None`` when it is bare.

    ``models: []`` and ``models: [{name: a, ...}]`` are values a hand-written
    config may legitimately carry, and the insertion path collapses both to a
    bare key. Dropping them on the way *in* as well is what keeps a load from
    reporting nothing while a save deletes them. A trailing comment is not part
    of the value — ``_split_inline_comment`` enforces the YAML rule that ``#``
    only opens a comment outside quotes and after whitespace.
    """
    body = line.rstrip("\r\n")
    value, _comment = _split_inline_comment(body[len("models:") :])
    return value.strip() or None


def _bare_models_key_line(line: str, newline: str) -> str:
    """Collapse an inline-valued ``models:`` line to a bare key.

    ``models: []  # hi`` → ``models:  # hi``: the inline value is dropped because
    the managed region supersedes it, while any trailing comment (and the line
    ending) is preserved. Lines already shaped as a bare key — with or without a
    comment — are returned unchanged, so well-formed configs stay byte-identical.
    """
    suffix = ""
    body = line
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            suffix, body = ending, line[: -len(ending)]
            break

    value, comment = _split_inline_comment(body[len("models:") :])
    if not value.strip():
        # Already a bare key (optionally with a comment) — leave it alone.
        normalised = body
    else:
        normalised = "models:" + (f" {comment}" if comment else "")

    if not suffix:
        # Unterminated final line: terminate it so the inserted region cannot
        # merge into the key.
        suffix = newline

    return normalised + suffix


def _split_inline_comment(text: str) -> tuple[str, str]:
    """Split *text* into ``(value, comment)``, comment including its leading ``#``.

    A ``#`` only opens a comment when it is outside quotes and preceded by
    whitespace, per the YAML comment rule — so ``["#nope"]`` and ``[a]#b`` keep
    their ``#`` in the value.
    """
    quote: str | None = None
    for index, char in enumerate(text):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and index > 0 and text[index - 1] in " \t":
            return text[:index], text[index:]
    return text, ""


def _find_marker(lines: list[str], marker: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() == marker:
            return index
    return None


def read_config_text(config_path: Path) -> str:
    """Read *config_path* WITHOUT newline translation.

    ``Path.read_text()`` / ``open(..., "r")`` default to universal-newlines
    mode, which silently rewrites every ``\\r\\n`` to ``\\n`` on the way in.
    That is fatal for this module's purpose: the surgical editor never touches
    lines outside the managed region, but if the *read* already normalised the
    line endings then writing the result changes every single line of the file.
    The byte-range guarantee would hold in the text domain while the file on
    disk changed wholesale — an unusable ``diff`` and an un-reviewable commit.

    Deployment configs are frequently CRLF (the target machine's is 100% CRLF),
    so this is the common case, not a corner case. ``newline=""`` disables the
    translation so the exact bytes round-trip.
    """
    with open(config_path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _detect_newline(lines: list[str]) -> str:
    """Return the dominant line ending in *lines* (default ``"\\n"``).

    Majority vote rather than "first terminated line": a file with one stray
    LF near the top but CRLF everywhere else should keep CRLF. Ties go to CRLF
    because Windows-targeted configs are the deployment reality, and a mixed
    file has no single right answer anyway.
    """
    crlf = sum(1 for line in lines if line.endswith("\r\n"))
    lf = sum(1 for line in lines if line.endswith("\n") and not line.endswith("\r\n"))
    if crlf == 0 and lf == 0:
        return "\n"
    return "\r\n" if crlf >= lf else "\n"


def _apply_newline(rendered: list[str], newline: str) -> list[str]:
    if newline == "\n":
        return rendered
    return [line.rstrip("\n") + newline for line in rendered]


# --------------------------------------------------------------------------- #
# Reading entries back out of the managed region
# --------------------------------------------------------------------------- #


def load_managed_models(config_path: Path) -> list[dict]:
    """Return the model dicts that make up the ``models:`` section of *config_path*.

    With the managed-region markers present, only the lines between them are
    parsed. The rest of the file — the ~1471 comment lines documenting operator
    intent — is never handed to the YAML parser, which is what makes a
    comment-heavy config cheap and safe to read. ``$VAR`` references are returned
    verbatim; resolving them is the caller's business (see ``to_public`` for the
    masking rules).

    Without the markers the top-level ``models:`` section is read directly, so a
    config written by hand still reports its models and the admin list is not
    empty. That is not a convenience: the save endpoints write back only the
    entries they were handed, so any entry this function withholds is one the
    next save deletes. Reading a marker-less section is therefore part of the
    same guarantee ``replace_managed_section`` makes — the two are complements
    (see :func:`_models_section_value_lines`).

    Returns an empty list when there is no ``models:`` section, when the value is
    empty or comment-only, or when the markers are half-present (a malformed file
    that ``replace_managed_section`` refuses to edit). Raises ``ValueError`` when
    the section exists but is not a YAML list of mappings.
    """
    lines = read_config_text(Path(config_path)).splitlines(keepends=True)

    begin = _find_marker(lines, MANAGED_BEGIN)
    end = _find_marker(lines, MANAGED_END)

    if begin is not None or end is not None:
        if begin is None or end is None or end < begin:
            return []
        return _parse_models_region("".join(lines[begin + 1 : end]), config_path)

    block = find_models_block(lines)
    if block is None:
        return []

    inline = _models_key_inline_value(lines[block[0]])
    if inline is not None:
        return _parse_models_region(inline, config_path)
    return _parse_models_region("".join(_models_section_value_lines(lines, block)), config_path)


def _parse_models_region(region: str, config_path: Path) -> list[dict]:
    """Parse *region* as the ``models:`` sequence, rejecting anything else.

    ``region`` holds only the section's value — markers and comment-only lines
    are stripped by the caller — so the common case for a template-shaped config
    is an empty string, which parses to nothing rather than to a confusing error.
    """
    if not region.strip():
        return []

    try:
        parsed = yaml.safe_load(region)
    except yaml.YAMLError as exc:
        raise ValueError(f"managed models region of {config_path} is not valid YAML: {exc}") from exc

    if parsed is None:
        return []
    if not isinstance(parsed, list):
        raise ValueError(f"managed models region of {config_path} must be a YAML list, got {type(parsed).__name__}")
    for index, entry in enumerate(parsed):
        if not isinstance(entry, Mapping):
            raise ValueError(f"managed models region of {config_path} entry #{index} must be a mapping, got {type(entry).__name__}")
    return [dict(entry) for entry in parsed]


# --------------------------------------------------------------------------- #
# Masking before an entry reaches a browser
# --------------------------------------------------------------------------- #


def to_public(model: dict) -> dict:
    """Return a deep copy of *model* with ``api_key`` masked for the browser.

    ``$VOLCENGINE_API_KEY`` is an environment *reference*, not a credential, so
    it is passed through unchanged — the UI needs it verbatim to show which
    variable an entry points at. A literal key keeps only enough of its edges to
    be recognisable; anything too short to hide a middle is masked entirely.
    """
    public = copy.deepcopy(model)
    api_key = public.get("api_key")
    if isinstance(api_key, str):
        public["api_key"] = _mask_secret(api_key)
    return public


def _mask_secret(value: str) -> str:
    """Mask a literal secret, preserving its first/last few characters."""
    if not value or value.startswith("$"):
        # An empty value has nothing to hide and a `$VAR` reference is a pointer.
        return value
    if len(value) < _MASK_REVEAL_MIN_LENGTH:
        return _MASK
    return value[:_MASK_PREFIX_LENGTH] + _MASK + value[-_MASK_SUFFIX_LENGTH:]


# --------------------------------------------------------------------------- #
# Building an entry from a UI payload
# --------------------------------------------------------------------------- #


def build_model_entry(payload: dict) -> dict:
    """Build a ``ModelConfig``-valid entry dict from a UI *payload*.

    The payload is copied, never mutated: the caller's dict may still be needed
    for error reporting. ``name`` defaults to the provider ``model`` id, which is
    how the commented examples in ``config.example.yaml`` are shaped.

    Raises ``ValueError`` when the payload is not a mapping, when ``use`` or
    ``model`` is missing/blank, or when a present field has the wrong type
    (``pydantic.ValidationError``, which subclasses ``ValueError``).
    """
    if not isinstance(payload, Mapping):
        raise ValueError(f"model payload must be a mapping, got {type(payload).__name__}")

    entry = dict(payload)
    missing = [field for field in REQUIRED_MODEL_FIELDS if not _has_text(entry.get(field))]
    if missing:
        raise ValueError(f"model payload is missing required field(s): {', '.join(missing)}")

    if not _has_text(entry.get("name")):
        entry["name"] = entry["model"]

    try:
        ModelConfig.model_validate(entry)
    except ValidationError as exc:
        # Re-raise as a plain `ValueError` (not the Pydantic type) so the API
        # layer has exactly one exception type to translate into a 400.
        raise ValueError(f"model payload is not a valid model entry: {exc}") from exc
    return entry


def _has_text(value: object) -> bool:
    """True when *value* is a non-blank string."""
    return isinstance(value, str) and bool(value.strip())


# --------------------------------------------------------------------------- #
# Validating a candidate before it is allowed anywhere near the live file
# --------------------------------------------------------------------------- #


def validate_candidate_text(candidate_text: str, *, dir_path: Path) -> AppConfig:
    """Parse *candidate_text* as a config via ``AppConfig.from_file``.

    The candidate is staged in a throwaway file directly inside *dir_path* (not
    a temp subdirectory) so that relative paths written in the config resolve
    against the same directory the live ``config.yaml`` lives in. The file is
    removed on both the success and the failure path.

    Raises ``ValueError`` for a YAML syntax error, an unresolvable ``$VAR``, an
    entry that ``ModelConfig`` rejects, or a model ``use`` path that does not
    resolve to a real class — the original file is never read or written here,
    so a rejected candidate costs nothing.
    """
    dir_path = Path(dir_path)
    handle, temp_name = tempfile.mkstemp(dir=dir_path, prefix=".config-candidate-", suffix=".tmp")
    os.close(handle)
    temp_path = Path(temp_name)

    try:
        temp_path.write_text(candidate_text, encoding="utf-8")
        try:
            parsed = AppConfig.from_file(temp_path)
        except yaml.YAMLError as exc:
            # `yaml.YAMLError` is not a `ValueError`; normalise so callers only
            # have one exception type to handle for "this candidate is bad".
            raise ValueError(f"candidate config is not valid YAML: {exc}") from exc
    finally:
        _discard(temp_path)

    # Only after the temp file is gone: resolving imports can be slow and can
    # fail on an optional dependency, and neither belongs on the staging path.
    _validate_model_use_paths(parsed)
    return parsed


def _validate_model_use_paths(config: AppConfig) -> None:
    """Reject a config whose model ``use`` paths do not resolve to real classes.

    ``ModelConfig.use`` is a bare ``str`` and nothing else checks it, so without
    this a typo'd provider is saved successfully and only blows up on the first
    chat turn, when ``create_chat_model`` calls ``resolve_class``. Rejecting at
    save time keeps the pre-write backup from being the user's only way back.

    Every managed entry is checked, not just the one being edited: the whole
    candidate is what gets written.

    ``base_class`` is deliberately left unset. Requiring ``BaseChatModel`` would
    also assert subclass *shape*, which is a stronger claim than "the class
    exists" — and the failure mode this guards against is a module/attribute typo,
    which the plain class check already catches.
    """
    for model in config.models:
        validate_model_entry(model)


def validate_model_entry(model: ModelConfig) -> None:
    """Check one entry's provider-specific settings against its provider class.

    Split out so a caller that only cares about a single candidate — the model
    probe, which must answer "would this entry work if saved?" without linting
    every unrelated entry in the file — can ask that question directly.

    Two checks, both of which decide whether the model can be built at all:

    - ``use`` resolves to a real class;
    - the endpoint key is one this provider class accepts.

    Raises ``ValueError`` with the same message a save would surface, so a probe
    and a save give the same answer for the same entry.
    """
    use_path = model.use
    try:
        cls = resolve_class(use_path)
    except Exception as exc:
        # ImportError/AttributeError from the resolver, plus anything an
        # imported provider module raises at import time (missing optional
        # dependency, bad platform-specific code). All mean "this path is
        # not usable"; none may escape as an unhandled 500.
        raise ValueError(f"model '{model.name}' has an unresolvable 'use' path {use_path!r}: {exc}") from exc
    _validate_base_url_key(model, cls)


#: Keys an entry may use to point a provider at a custom endpoint. The name is
#: per-provider (``openai_api_base`` / ``anthropic_api_url`` / ``base_url`` /
#: ``api_base``), so a wrong guess cannot be detected structurally — only the
#: provider class knows. Kept here as the single list both the check and the
#: error message reason about.
_BASE_URL_KEYS = ("api_base", "base_url", "openai_api_base", "anthropic_api_url")


def _validate_base_url_key(model: ModelConfig, cls: type) -> None:
    """Reject an entry whose base-URL key its provider does not accept.

    ``ModelConfig`` is ``extra="allow"``, so an unrecognised key is accepted at
    save time and forwarded into the provider's ``model_kwargs``. The failure
    surfaces only on the first real request, as a confusing SDK error::

        AsyncMessages.create() got an unexpected keyword argument 'api_base'

    That is exactly the trap a shared ``api_base`` key creates: only some
    providers accept it (the ``deerflow.models.patched_*`` ones do;
    ``langchain_anthropic`` wants ``anthropic_api_url``, ``langchain_openai``
    wants ``openai_api_base``, ``langchain_google_genai`` wants ``base_url``).

    Warning, not an error, when the provider accepts *none* of the known keys:
    those providers (e.g. patched MiniMax) take the endpoint through
    ``model_kwargs``, and the config may well be intentional. A key the provider
    *demonstrably* rejects is unambiguous, though, so that is a hard failure.
    """
    supplied = [key for key in _BASE_URL_KEYS if getattr(model, key, None) is not None]
    if not supplied:
        return

    fields = getattr(cls, "model_fields", None)
    if not isinstance(fields, dict):  # not a pydantic model — cannot judge
        return

    accepted = [key for key in supplied if key in fields]
    if accepted:
        return

    # Nothing the entry supplied is a real field. If the class exposes some
    # other base-URL field, the entry almost certainly meant that one.
    hint = next((name for name in fields if "url" in name.lower() or name.lower() == "base_url"), None)
    suggestion = f" Use '{hint}' instead." if hint else ""
    raise ValueError(f"model '{model.name}' sets {supplied} but provider '{model.use}' accepts none of them.{suggestion} An unrecognised key is silently forwarded to the SDK and only fails when the model is first called.")


# --------------------------------------------------------------------------- #
# Committing: backup first, then a single atomic replace
# --------------------------------------------------------------------------- #


def commit_config_update(config_path: Path, new_text: str, *, keep: int = DEFAULT_BACKUP_KEEP, now: datetime | None = None) -> Path:
    """Back up *config_path*, then atomically replace it with *new_text*.

    Returns the path of the backup, which holds the pre-replace bytes — the
    safety net for a problem validation cannot catch (a provider field that only
    fails on a real request). ``now`` is injectable so backup names are
    reproducible in tests.

    The original is modified at exactly one point — the final ``os.replace`` —
    and only after the backup has been written. If the backup, the staging write,
    or the replace fails, both the staging file and the just-written backup are
    removed and the exception propagates, leaving the directory as it was found.
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        raise FileNotFoundError(f"cannot commit config update: {config_path} does not exist")

    original = config_path.read_bytes()

    backup_path = _create_backup(config_path, original, now)

    work_path = config_path.with_name(config_path.name + WORK_SUFFIX)
    try:
        with open(work_path, "wb") as handle:
            handle.write(new_text.encode("utf-8"))
        os.replace(work_path, config_path)
    except BaseException:
        # Nothing durable happened yet: the original is still in place, so the
        # backup is redundant and must not be left behind as evidence of a write
        # that never landed.
        _discard(work_path)
        _discard(backup_path)
        raise

    # Pruning sits *outside* the block above on purpose. Now that the replace has
    # landed, the backup is the only copy of the pre-commit original — rolling it
    # back from that handler would destroy the very file this function exists to
    # protect. A prune failure must never look like a failed commit.
    try:
        prune_backups(config_path, keep=keep)
    except OSError:
        logger.warning("Could not prune old backups of %s", config_path, exc_info=True)

    return backup_path


def _iter_backups(config_path: Path) -> list[tuple[str, int, Path]]:
    """Return ``(timestamp, sequence, path)`` for every backup of *config_path*.

    Only ``<config>.bak.<YYYYMMDD-HHMMSS>`` counts, with an optional ``-N`` when
    more than one commit landed inside the same second. The timestamp is read
    from the *name* rather than the mtime, so pruning a copied or restored
    directory behaves identically. Adjacent files that merely look similar —
    ``config.yaml.bak``, ``config.yaml.tmp``, ``notes.txt``, another config's
    backups — are excluded by the anchored pattern.
    """
    pattern = re.compile(rf"^{re.escape(config_path.name)}\.bak\.{_BACKUP_STAMP_RE.pattern}$")

    found: list[tuple[str, int, Path]] = []
    for entry in config_path.parent.iterdir():
        match = pattern.match(entry.name)
        if match is not None and entry.is_file():
            found.append((match.group(1), int(match.group(2) or 0), entry))
    return found


def _create_backup(config_path: Path, original: bytes, now: datetime | None = None) -> Path:
    """Claim a fresh timestamped name for *config_path* and write *original* to it.

    Writes through ``builtins.open`` (not ``Path.write_bytes``) so a failing or
    guarded filesystem raises here — *before* the live file is touched — rather
    than after the replace. The ``"xb"`` mode makes the claim atomic, so two
    concurrent commits cannot both land on one backup name.
    """
    stamp = (now or datetime.now()).strftime(_BACKUP_STAMP_FORMAT)
    taken = [sequence for moment, sequence, _path in _iter_backups(config_path) if moment == stamp]

    index = max(taken, default=-1) + 1
    while True:
        path = _indexed_backup_path(config_path, stamp, index)
        try:
            handle = open(path, "xb")
        except FileExistsError:
            # Another writer claimed this name between the scan and here.
            index += 1
            continue
        try:
            with handle:
                handle.write(original)
        except BaseException:
            # A half-written backup is not a backup — e.g. ENOSPC after the
            # file was created but before the bytes landed. Remove it rather
            # than leave something a rollback would trust.
            _discard(path)
            raise
        return path


def _indexed_backup_path(config_path: Path, stamp: str, index: int) -> Path:
    """``config.yaml.bak.<stamp>`` for index 0, ``...-N`` beyond it."""
    name = f"{config_path.name}.bak.{stamp}"
    return config_path.with_name(name if index == 0 else f"{name}-{index}")


def prune_backups(config_path: Path, keep: int = DEFAULT_BACKUP_KEEP) -> list[Path]:
    """Delete the oldest ``config.yaml.bak.*`` for *config_path* beyond *keep*.

    Ordering comes from the timestamp (and same-second sequence) in the *name*,
    not from mtime, so a copied or restored directory prunes identically. Files
    that merely sit next to the config — ``config.yaml.bak``, ``notes.txt``,
    another file's backups — are never touched.
    """
    config_path = Path(config_path)
    found = _iter_backups(config_path)
    found.sort(key=lambda item: (item[0], item[1]))

    excess = len(found) - max(keep, 0)
    if excess <= 0:
        return []

    deleted: list[Path] = []
    for _moment, _sequence, path in found[:excess]:
        _discard(path)
        deleted.append(path)
    return deleted


def _discard(path: Path) -> None:
    """Remove *path* if it exists, tolerating a concurrent removal."""
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - defensive; a leftover file is not fatal
        logger.warning("Could not remove staging file %s", path, exc_info=True)


# --------------------------------------------------------------------------- #
# Writing a new credential: the dotenv file, then the running process
# --------------------------------------------------------------------------- #


def upsert_env_var(env_path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in the dotenv file at *env_path*, then make it visible to
    the running process via ``os.environ``.

    Both halves are one operation on purpose. ``load_dotenv()`` runs once, at
    import time (``app_config.py:44``), so a key appended to ``.env`` after
    startup is invisible to the running process: ``$VAR`` resolves to ``None`` and
    ``resolve_env_variables`` raises. Because config reload has no exception
    guard, that one unresolvable reference would break every subsequent request.
    Updating ``os.environ`` here closes that window.

    ``os.environ`` is assigned rather than defaulted, so a stale in-process value
    cannot shadow the key the operator just saved. ``load_dotenv()`` never
    overrides an existing variable, so once the process picks the value up from
    the file on the next restart, memory and disk agree.

    Like the config editor this is a line-level edit: an existing assignment is
    rewritten in place and every other line — comments, blank lines, unrelated
    variables, the file's line ending style and permissions — is preserved
    byte-for-byte. A missing key is appended. The write itself goes through a
    temp file plus ``os.replace`` so a failed write cannot leave a truncated
    ``.env`` behind, which would cost the operator every other credential in it.

    Values needing quotes are quoted the way ``Write-DotEnv`` in
    ``scripts/windows/DeerFlow.Common.psm1`` does it — double quotes, except that
    a value carrying a double quote *or* a backslash is single-quoted instead,
    since python-dotenv expands escape sequences inside double quotes (a Windows
    path like ``C:\\dir\\note`` would come back with a real newline in it).

    Raises ``ValueError`` for a key that is not a valid environment variable name
    or a value that is not a single-line string.
    """
    if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key):
        raise ValueError(f"invalid environment variable name: {key!r}")
    if not isinstance(value, str):
        raise ValueError(f"environment variable {key} must be a string, got {type(value).__name__}")
    if "\n" in value or "\r" in value:
        raise ValueError(f"environment variable {key} must not contain a newline")

    env_path = Path(env_path)
    # Bytes, not `read_text`: Python's text mode translates newlines, which would
    # silently convert a CRLF file (what the Windows deployment's `Write-DotEnv`
    # produces) to LF and mangle it on the way back out.
    text = env_path.read_bytes().decode("utf-8") if env_path.is_file() else ""
    newline = _detect_newline(text.splitlines(keepends=True))

    _write_env_text(env_path, _upsert_env_line(text, key, f"{key}={_format_env_value(value)}", newline))

    # Only after the value is durable on disk. A process-local value with no
    # matching file entry would vanish on restart, silently reverting the key.
    os.environ[key] = value


def _upsert_env_line(text: str, key: str, assignment: str, newline: str) -> str:
    """Return *text* with *key*'s assignment set to *assignment*.

    Only the last assignment is rewritten: python-dotenv keeps the last one, so
    rewriting an earlier duplicate would leave the stale value in charge. A key
    that is absent is appended, terminating an unterminated final line first —
    appending blindly would fuse the two into one bogus entry.
    """
    # `KEY=` only: the trailing `[ \t]*=` is what keeps OPENAI_API_KEY from
    # matching OPENAI_API_KEY_EXTRA.
    pattern = re.compile(rf"^(?:export[ \t]+)?{re.escape(key)}[ \t]*=")
    lines = text.splitlines(keepends=True)

    target: int | None = None
    for index, line in enumerate(lines):
        if pattern.match(line):
            target = index

    if target is not None:
        lines[target] = assignment + newline
        return "".join(lines)

    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline
    return "".join(lines) + assignment + newline


def _format_env_value(value: str) -> str:
    """Quote *value* the way the Windows deployment's ``Write-DotEnv`` does."""
    if value == "" or re.search(r"[\s#]", value):
        if '"' in value or "\\" in value:
            # Double quotes expand escapes; a path would lose its backslashes.
            if "'" not in value:
                return f"'{value}'"
            # Neither quote character can wrap this value safely.
            logger.warning("Environment value contains both quote characters; writing it unquoted")
            return value
        return f'"{value}"'
    return value


def _write_env_text(env_path: Path, new_text: str) -> None:
    """Atomically replace *env_path* with *new_text*.

    The temp file is created in the target directory so ``os.replace`` stays on
    one filesystem. An existing file's mode is carried over; a new one gets the
    mode a plain ``open()`` would have produced, so creating ``.env`` here looks
    no different from an operator creating it by hand.
    """
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = (env_path.stat().st_mode & 0o777) if env_path.is_file() else None

    handle, temp_name = tempfile.mkstemp(dir=env_path.parent, prefix=".env-upsert-", suffix=".tmp")
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        temp_path.write_bytes(new_text.encode("utf-8"))
        os.chmod(temp_path, existing_mode if existing_mode is not None else _default_file_mode())
        os.replace(temp_path, env_path)
    except BaseException:
        _discard(temp_path)
        raise


def _default_file_mode() -> int:
    """The mode a plain ``open(path, "w")`` would create: ``0o666`` minus umask."""
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask
