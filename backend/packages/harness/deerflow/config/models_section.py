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

logger = logging.getLogger(__name__)

MANAGED_BEGIN = "# >>> DeerFlow Web UI 托管区域 — 界面会整体重写，请勿手工编辑 >>>"
MANAGED_END = "# <<< DeerFlow Web UI 托管区域结束 <<<"

#: A top-level mapping key: starts at column 0 with a letter or underscore.
_TOP_LEVEL_KEY_RE = re.compile(r"[a-zA-Z_]")

_MODELS_KEY_RE = re.compile(r"models:")

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
    line, above the commented templates. Everything else is preserved verbatim,
    including line endings. Raises ``ValueError`` when the file has no ``models:``
    section or carries only one of the two markers.
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
        return "".join(lines[:anchor] + rendered + lines[anchor:])

    if begin is None or end is None or end < begin:
        raise ValueError(f"config text has unbalanced managed-region markers ({MANAGED_BEGIN!r} / {MANAGED_END!r})")

    if block is not None and block[0] < begin:
        # Same inline-value hazard on the already-marked path: `models: []` above
        # an existing region is just as unparseable as one above an inserted one.
        lines[block[0]] = _bare_models_key_line(lines[block[0]], newline)

    return "".join(lines[:begin] + rendered + lines[end + 1 :])


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


def _detect_newline(lines: list[str]) -> str:
    """Return the line ending used by the first terminated line (default ``"\\n"``)."""
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def _apply_newline(rendered: list[str], newline: str) -> list[str]:
    if newline == "\n":
        return rendered
    return [line.rstrip("\n") + newline for line in rendered]


# --------------------------------------------------------------------------- #
# Reading entries back out of the managed region
# --------------------------------------------------------------------------- #


def load_managed_models(config_path: Path) -> list[dict]:
    """Return the model dicts inside the managed region of *config_path*.

    Only the lines between the two markers are parsed. The rest of the file —
    the ~1471 comment lines documenting operator intent — is never handed to the
    YAML parser, which is what makes a comment-heavy config cheap and safe to
    read. ``$VAR`` references are returned verbatim; resolving them is the
    caller's business (see ``to_public`` for the masking rules).

    Returns an empty list when the region is absent, empty, or half-marked.
    Raises ``ValueError`` when the region exists but is not a YAML list of
    mappings.
    """
    text = Path(config_path).read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    begin = _find_marker(lines, MANAGED_BEGIN)
    end = _find_marker(lines, MANAGED_END)
    if begin is None or end is None or end < begin:
        return []

    region = "".join(lines[begin + 1 : end])
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

    Raises ``ValueError`` for a YAML syntax error, an unresolvable ``$VAR``, or
    an entry that ``ModelConfig`` rejects — the original file is never read or
    written here, so a rejected candidate costs nothing.
    """
    dir_path = Path(dir_path)
    handle, temp_name = tempfile.mkstemp(dir=dir_path, prefix=".config-candidate-", suffix=".tmp")
    os.close(handle)
    temp_path = Path(temp_name)

    try:
        temp_path.write_text(candidate_text, encoding="utf-8")
        try:
            return AppConfig.from_file(temp_path)
        except yaml.YAMLError as exc:
            # `yaml.YAMLError` is not a `ValueError`; normalise so callers only
            # have one exception type to handle for "this candidate is bad".
            raise ValueError(f"candidate config is not valid YAML: {exc}") from exc
    finally:
        _discard(temp_path)


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
