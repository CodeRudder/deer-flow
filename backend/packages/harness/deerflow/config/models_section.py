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
"""

from __future__ import annotations

import re

import yaml

MANAGED_BEGIN = "# >>> DeerFlow Web UI 托管区域 — 界面会整体重写，请勿手工编辑 >>>"
MANAGED_END = "# <<< DeerFlow Web UI 托管区域结束 <<<"

#: A top-level mapping key: starts at column 0 with a letter or underscore.
_TOP_LEVEL_KEY_RE = re.compile(r"[a-zA-Z_]")

_MODELS_KEY_RE = re.compile(r"models:")

_INDENT = "  "


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
