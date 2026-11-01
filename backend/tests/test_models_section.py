"""Tests for the surgical ``models:`` section editor.

The editor must never reserialize config.yaml: the file is 80% comments that
document operator intent, and ``yaml.dump`` would erase them. Every assertion
about "byte-identical outside the managed region" below is the whole point of
the module — a destructive rewrite would silently delete ~1471 comment lines.

The second half covers the read/mask/validate/commit helpers the admin model
endpoints are built on. The load-bearing property there is that **no failure
path may modify ``config.yaml``**: validation happens on a copy, and the
original is only ever replaced after a timestamped backup has been written.

The last section covers ``upsert_env_var``. ``load_dotenv()`` runs once at import
(``app_config.py:44``), so a key appended to ``.env`` is invisible to the running
process — ``$NEW_KEY`` would resolve to ``None`` and ``resolve_env_variables``
would raise. Because config reload has no exception guard, that one bad reload
would break every subsequent request. These tests therefore pin the full
ordering constraint: the ``.env`` write has to land *before* the candidate config
is validated.
"""

from __future__ import annotations

import builtins
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

from deerflow.config import models_section
from deerflow.config.app_config import AppConfig
from deerflow.config.models_section import (
    DEFAULT_BACKUP_KEEP,
    MANAGED_BEGIN,
    MANAGED_END,
    _detect_newline,
    build_model_entry,
    commit_config_update,
    find_models_block,
    load_managed_models,
    prune_backups,
    read_config_text,
    render_managed_section,
    replace_managed_section,
    to_public,
    upsert_env_var,
    validate_candidate_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_EXAMPLE = REPO_ROOT / "config.example.yaml"

# A miniature stand-in for the real file: top-level keys, heavy comments, and a
# block of commented-out templates under ``models:``.
SAMPLE_TEXT = """\
# ============================================================================
# Models Configuration
# ============================================================================

log_level: info

models:
  # Example: Volcengine (Doubao) model
  # - name: doubao-seed-1.8
  #   display_name: Doubao-Seed-1.8
  #   use: deerflow.models.patched_deepseek:PatchedChatDeepSeek

  # Example: OpenAI model
  # - name: gpt-4
  #   use: langchain_openai:ChatOpenAI

vision:
  # vision settings
  enabled: true

tools:
  - name: web_search
"""

SAMPLE_MODELS = [
    {
        "name": "doubao-seed-1.8",
        "display_name": "Doubao-Seed-1.8",
        "use": "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
        "model": "doubao-seed-1-8-251228",
        "api_key": "$VOLCENGINE_API_KEY",
        "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
        "max_tokens": 4096,
        "supports_vision": True,
    },
    {
        "name": "gpt-4",
        "use": "langchain_openai:ChatOpenAI",
        "model": "gpt-4",
        "api_key": "$OPENAI_API_KEY",
    },
]


def _write_then_replace(text: str, models: list[dict]) -> tuple[list[str], list[str], tuple[int, int], tuple[int, int]]:
    """Replace and return (input_lines, output_lines, input_region, output_region).

    ``input_region`` is the half-open line range the editor may delete from the
    input; ``output_region`` is the half-open line range it may write into the
    output. Everything else must be byte-identical.
    """
    in_lines = text.splitlines(keepends=True)
    out_text = replace_managed_section(text, models)
    out_lines = out_text.splitlines(keepends=True)

    out_begin = next(i for i, line in enumerate(out_lines) if line.strip() == MANAGED_BEGIN)
    out_end = next(i for i, line in enumerate(out_lines) if line.strip() == MANAGED_END)
    out_region = (out_begin, out_end + 1)

    in_begin = next((i for i, line in enumerate(in_lines) if line.strip() == MANAGED_BEGIN), None)
    in_end = next((i for i, line in enumerate(in_lines) if line.strip() == MANAGED_END), None)
    if in_begin is not None and in_end is not None:
        in_region = (in_begin, in_end + 1)
    else:
        # No markers yet: the editor inserts right after the ``models:`` line.
        block = find_models_block(in_lines)
        assert block is not None
        anchor = block[0] + 1
        in_region = (anchor, anchor)

    return in_lines, out_lines, in_region, out_region


def _assert_outside_region_identical(text: str, models: list[dict]) -> list[str]:
    """Core invariant: every line outside the managed region is unchanged."""
    in_lines, out_lines, in_region, out_region = _write_then_replace(text, models)

    assert out_lines[: out_region[0]] == in_lines[: in_region[0]], "lines before the managed region changed"
    assert out_lines[out_region[1] :] == in_lines[in_region[1] :], "lines after the managed region changed"
    # Nothing outside the region may be added or removed either.
    assert len(out_lines) - (out_region[1] - out_region[0]) == len(in_lines) - (in_region[1] - in_region[0])
    return out_lines


# --------------------------------------------------------------------------- #
# find_models_block
# --------------------------------------------------------------------------- #


def test_find_models_block_returns_top_level_range():
    lines = SAMPLE_TEXT.splitlines(keepends=True)

    block = find_models_block(lines)

    assert block is not None
    start, end = block
    assert lines[start].startswith("models:")
    assert lines[end].startswith("vision:")
    assert end == 16


def test_find_models_block_returns_none_when_absent():
    lines = ["log_level: info\n", "vision:\n", "  enabled: true\n"]

    assert find_models_block(lines) is None


def test_find_models_block_ignores_indented_models_key():
    """A nested ``models:`` (e.g. under an agent) is not the top-level section."""
    lines = [
        "agents:\n",
        "  researcher:\n",
        "    models:\n",
        "      - name: nested\n",
        "vision:\n",
    ]

    assert find_models_block(lines) is None

    with_tools = ["tools:\n", "  - name: t\n", "    models:\n", "      - name: nested\n"]
    assert find_models_block(with_tools) is None


def test_find_models_block_extends_to_eof_when_last_top_level_key():
    lines = [
        "log_level: info\n",
        "models:\n",
        "  - name: a\n",
        "  # trailing comment\n",
    ]

    assert find_models_block(lines) == (1, len(lines))


def test_find_models_block_skips_comments_and_blank_lines_after_models():
    lines = ["models:\n", "\n", "# a comment\n", "  # indented comment\n", "vision:\n", "  x: 1\n"]

    assert find_models_block(lines) == (0, 4)


def test_find_models_block_accepts_crlf_lines():
    lines = SAMPLE_TEXT.replace("\n", "\r\n").splitlines(keepends=True)

    block = find_models_block(lines)

    assert block is not None
    assert block[0] == 6


# --------------------------------------------------------------------------- #
# render_managed_section
# --------------------------------------------------------------------------- #


def test_render_includes_both_markers_and_two_space_indent():
    rendered = render_managed_section(SAMPLE_MODELS)

    assert rendered[0].strip() == MANAGED_BEGIN
    assert rendered[-1].strip() == MANAGED_END
    assert rendered[0].endswith("\n") and rendered[-1].endswith("\n")

    body = rendered[1:-1]
    assert body, "expected rendered body lines"
    assert body[0].startswith("  - "), "entries must be list items indented by two spaces"
    for line in body:
        indent = len(line) - len(line.lstrip(" "))
        assert indent >= 2 and indent % 2 == 0, line
        assert "\t" not in line, line


def test_render_empty_models_keeps_markers():
    assert [line.strip() for line in render_managed_section([])] == [MANAGED_BEGIN, MANAGED_END]


def test_render_output_is_valid_yaml_round_trip():
    rendered = render_managed_section(SAMPLE_MODELS)

    # The blank line between the marker and the first entry is allowed fine.
    parsed = yaml.safe_load("\n".join(line.rstrip("\n") for line in rendered[1:-1]))

    assert parsed == SAMPLE_MODELS


def test_render_matches_config_example_style():
    """Nested mappings indent 2 spaces per level, exactly like config.example.yaml."""
    rendered = "".join(render_managed_section(SAMPLE_MODELS))
    lines = rendered.splitlines()

    assert "  - name: doubao-seed-1.8" in lines
    assert "    display_name: Doubao-Seed-1.8" in lines
    assert "    api_key: $VOLCENGINE_API_KEY" in lines
    assert "    when_thinking_enabled:" in lines
    assert "      extra_body:" in lines
    assert "        thinking:" in lines
    assert "          type: enabled" in lines


# --------------------------------------------------------------------------- #
# replace_managed_section — insertion path
# --------------------------------------------------------------------------- #


def test_inserts_below_models_line_and_above_templates():
    in_lines = SAMPLE_TEXT.splitlines(keepends=True)
    out_lines = replace_managed_section(SAMPLE_TEXT, SAMPLE_MODELS).splitlines(keepends=True)

    models_index = next(i for i, line in enumerate(out_lines) if line.startswith("models:"))
    assert out_lines[models_index + 1].strip() == MANAGED_BEGIN

    end_index = next(i for i, line in enumerate(out_lines) if line.strip() == MANAGED_END)
    # The commented templates survive, directly below the managed region.
    assert out_lines[end_index + 1] == "  # Example: Volcengine (Doubao) model\n"
    assert "  # - name: doubao-seed-1.8\n" in out_lines
    assert "  # - name: gpt-4\n" in out_lines
    # ...and no input line was lost.
    assert len(out_lines) > len(in_lines)


# The regression this pins: a config whose `models:` section holds *real*
# entries and no markers. The insertion path used to splice the managed region
# in below `models:` without removing what was already there, producing two
# sequences under one key — invalid YAML, so every save through the Web UI
# failed with "candidate config is not valid YAML" until the operator
# hand-added the markers.
UNMARKED_WITH_ENTRIES = """\
config_version: 26
log_level: info
models:
- name: existing
  use: langchain_anthropic:ChatAnthropic
  model: glm-5.1
  api_key: sk-existing
default_model: existing
search:
  enabled: true
"""


def test_insertion_replaces_real_entries_in_an_unmarked_section():
    out = replace_managed_section(UNMARKED_WITH_ENTRIES, SAMPLE_MODELS)

    # The whole point: the result must be parseable.
    parsed = yaml.safe_load(out)
    assert [entry["name"] for entry in parsed["models"]] == [
        "doubao-seed-1.8",
        "gpt-4",
    ]
    # The old entry is gone, not merely shadowed.
    assert "sk-existing" not in out
    # Everything outside the section is untouched.
    assert parsed["default_model"] == "existing"
    assert parsed["search"] == {"enabled": True}
    assert parsed["config_version"] == 26


def test_insertion_keeps_real_entries_of_neighbouring_sections():
    """Only the ``models:`` section is taken over — a sibling list survives."""
    text = """\
models:
- name: existing
  use: x:Y
  model: m
other:
- keep-me
"""
    parsed = yaml.safe_load(replace_managed_section(text, SAMPLE_MODELS))

    assert parsed["other"] == ["keep-me"]
    assert [entry["name"] for entry in parsed["models"]] == [
        "doubao-seed-1.8",
        "gpt-4",
    ]


def test_insertion_then_replace_is_stable_for_unmarked_entries():
    once = replace_managed_section(UNMARKED_WITH_ENTRIES, SAMPLE_MODELS)
    twice = replace_managed_section(once, SAMPLE_MODELS)

    assert twice == once


def test_insertion_preserves_comments_outside_region():
    _assert_outside_region_identical(SAMPLE_TEXT, SAMPLE_MODELS)


def test_insertion_preserves_blank_lines_and_comments_byte_for_byte():
    out_lines = _assert_outside_region_identical(SAMPLE_TEXT, SAMPLE_MODELS)

    assert "# ============================================================================\n" in out_lines
    assert "# Models Configuration\n" in out_lines
    assert "\n" in out_lines


def test_second_replace_rewrites_region_in_place():
    once = replace_managed_section(SAMPLE_TEXT, SAMPLE_MODELS)
    twice = replace_managed_section(once, SAMPLE_MODELS)

    assert twice == once


def test_replacement_only_touches_between_markers():
    once = replace_managed_section(SAMPLE_TEXT, SAMPLE_MODELS)
    twice = replace_managed_section(once, [])

    in_lines = once.splitlines(keepends=True)
    out_lines = twice.splitlines(keepends=True)
    in_begin = next(i for i, line in enumerate(in_lines) if line.strip() == MANAGED_BEGIN)
    in_end = next(i for i, line in enumerate(in_lines) if line.strip() == MANAGED_END)
    out_begin = next(i for i, line in enumerate(out_lines) if line.strip() == MANAGED_BEGIN)
    out_end = next(i for i, line in enumerate(out_lines) if line.strip() == MANAGED_END)

    assert out_lines[:out_begin] == in_lines[:in_begin]
    assert out_lines[out_end + 1 :] == in_lines[in_end + 1 :]
    assert out_end == out_begin + 1  # empty region: markers only
    assert out_lines[out_begin].strip() == MANAGED_BEGIN
    assert out_lines[out_end].strip() == MANAGED_END


def test_empty_models_replacement_keeps_markers_and_drops_entries():
    once = replace_managed_section(SAMPLE_TEXT, SAMPLE_MODELS)
    twice = replace_managed_section(once, [])

    assert "  - name: doubao-seed-1.8" in once
    assert "  - name: doubao-seed-1.8" not in twice
    assert MANAGED_BEGIN in twice and MANAGED_END in twice
    _assert_outside_region_identical(once, [])


# --------------------------------------------------------------------------- #
# replace_managed_section — byte-identical guarantee
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("models", [SAMPLE_MODELS, [], [{"name": "solo", "use": "x:Y", "model": "m"}]])
def test_outside_region_is_byte_identical_on_insert(models):
    _assert_outside_region_identical(SAMPLE_TEXT, models)


@pytest.mark.parametrize("models", [SAMPLE_MODELS, []])
def test_outside_region_is_byte_identical_on_replace(models):
    text = replace_managed_section(SAMPLE_TEXT, [{"name": "old", "use": "x:Y", "model": "m"}])
    _assert_outside_region_identical(text, models)


def test_all_comment_lines_survive_on_real_config_example():
    """Acceptance: on the real 1821-line example, only the region gains lines."""
    if not CONFIG_EXAMPLE.exists():  # pragma: no cover - repo layout guard
        pytest.skip("config.example.yaml not found")

    original = CONFIG_EXAMPLE.read_text(encoding="utf-8")
    out_lines = _assert_outside_region_identical(original, SAMPLE_MODELS)

    assert "doubao-seed-1.8" in "".join(out_lines)
    assert MANAGED_BEGIN in "".join(out_lines)

    # Every non-managed input line still exists verbatim, in order.
    in_lines = original.splitlines(keepends=True)
    in_begin, in_end = find_models_block(in_lines)  # type: ignore[misc]
    out_begin = next(i for i, line in enumerate(out_lines) if line.strip() == MANAGED_BEGIN)
    out_end = next(i for i, line in enumerate(out_lines) if line.strip() == MANAGED_END)
    # Insertion sits directly below `models:`; the whole tail (templates, then
    # `vision:` onwards) is untouched.
    assert out_lines[:out_begin] == in_lines[: in_begin + 1]
    assert out_lines[out_end + 1 :] == in_lines[in_begin + 1 :]


def test_crlf_line_endings_are_preserved():
    text = SAMPLE_TEXT.replace("\n", "\r\n")

    out_lines = _assert_outside_region_identical(text, SAMPLE_MODELS)
    out_text = "".join(out_lines)

    assert "\r\n" in out_text
    # No lone LF was introduced anywhere: every newline is part of a CRLF pair.
    assert out_text.count("\n") == out_text.count("\r\n")
    for line in out_lines[:3] + out_lines[-3:]:
        assert line.endswith("\r\n"), line
    # And the managed region itself uses CRLF too.
    begin = next(i for i, line in enumerate(out_lines) if line.strip() == MANAGED_BEGIN)
    assert out_lines[begin].endswith("\r\n")
    assert out_lines[begin + 1].endswith("\r\n")


def test_crlf_replacement_keeps_line_endings():
    text = replace_managed_section(SAMPLE_TEXT.replace("\n", "\r\n"), SAMPLE_MODELS)

    out_text = replace_managed_section(text, [])

    assert out_text.count("\n") == out_text.count("\r\n")


def test_lf_file_stays_lf():
    out_text = replace_managed_section(SAMPLE_TEXT, SAMPLE_MODELS)

    assert "\r" not in out_text


def test_unterminated_models_line_is_terminated_then_region_inserted():
    """The one documented exception to byte-identity.

    A file whose last line is an unterminated ``models:`` cannot receive an
    inserted region without first terminating that line — otherwise the output
    would read ``models:# >>> ...`` and be invalid YAML. Every other input shape
    is preserved verbatim.
    """
    text = "log_level: info\nmodels:"

    out = replace_managed_section(text, SAMPLE_MODELS)

    lines = out.splitlines(keepends=True)
    assert lines[0] == "log_level: info\n"
    assert lines[1] == "models:\n"
    assert lines[2].strip() == MANAGED_BEGIN
    assert yaml.safe_load(out)["models"] == SAMPLE_MODELS


def test_terminated_models_line_is_never_touched():
    text = "log_level: info\nmodels:\n"

    out_lines = _assert_outside_region_identical(text, SAMPLE_MODELS)

    assert out_lines[1] == "models:\n"


def test_file_without_trailing_newline_keeps_last_line_verbatim():
    text = "log_level: info\nmodels:\n  # template\n  # - name: a"

    out_lines = _assert_outside_region_identical(text, SAMPLE_MODELS)

    assert out_lines[-1] == "  # - name: a"


def test_no_models_key_raises():
    with pytest.raises(ValueError):
        replace_managed_section("log_level: info\nvision:\n  enabled: true\n", SAMPLE_MODELS)


def test_malformed_markers_raise():
    text = f"models:\n  {MANAGED_BEGIN}\n  - name: a\n"

    with pytest.raises(ValueError):
        replace_managed_section(text, SAMPLE_MODELS)


def test_replacing_when_models_section_is_last_key():
    text = "log_level: info\nmodels:\n  # template\n"

    out = replace_managed_section(text, SAMPLE_MODELS)

    assert out.startswith("log_level: info\nmodels:\n")
    # The region is inserted above the pre-existing trailing comment, which is
    # still the last line of the file.
    assert out.index(MANAGED_BEGIN) < out.index("  # template\n")
    assert out.endswith("  # template\n")
    _assert_outside_region_identical(text, SAMPLE_MODELS)


def test_unicode_display_names_survive():
    models = [{"name": "doubao", "display_name": "豆包 · Seed", "use": "x:Y", "model": "m"}]

    out = replace_managed_section(SAMPLE_TEXT, models)

    assert "豆包 · Seed" in out
    parsed = yaml.safe_load("\n".join(line.rstrip("\n") for line in out.splitlines() if not line.strip().startswith("#")))
    assert parsed["models"] == models


# --------------------------------------------------------------------------- #
# Inline values on the `models:` line
#
# `models: []` carries a flow value on the key line itself. Inserting a block
# sequence after it produces `models: []` followed by `  - name: x`, which YAML
# rejects ("expected <block end>, but found '<block sequence start>'"). Since the
# app reloads config without an exception guard, that would break every request.
# The editor must normalise such a key line back to a bare `models:` before
# inserting, discarding the value (the managed region supersedes it) while
# keeping any trailing comment.
# --------------------------------------------------------------------------- #

INLINE_KEY_LINES = [
    "models: []",
    "models: null",
    "models: []  # hi",
    "models:  # hi",
    "models:",
]

#: Key lines that carry an actual value which must be dropped on normalisation.
INLINE_VALUE_CASES = ["models: []", "models: null", "models: []  # hi"]


def _has_inline_value(line: str) -> bool:
    rest = line.split("models:", 1)[1].strip()
    return bool(rest) and not rest.startswith("#")


def _assert_only_models_key_line_changed(text: str, models: list[dict]) -> tuple[list[str], str]:
    """Assert byte-identity everywhere except the `models:` key line.

    Returns ``(output_lines, normalised_key_line)``. The key line is the one
    documented exception: an inline value cannot coexist with the inserted
    block sequence, so it is collapsed to a bare key.
    """
    in_lines = text.splitlines(keepends=True)
    out_lines = replace_managed_section(text, models).splitlines(keepends=True)

    key_index = find_models_block(in_lines)[0]
    out_begin = next(i for i, line in enumerate(out_lines) if line.strip() == MANAGED_BEGIN)
    out_end = next(i for i, line in enumerate(out_lines) if line.strip() == MANAGED_END)

    # The key line is the last line before the region...
    assert out_lines[out_begin - 1].startswith("models:"), out_lines[out_begin - 1]
    # ...everything before it is untouched...
    assert out_lines[: out_begin - 1] == in_lines[:key_index]
    # ...and everything after the region is the original tail, verbatim.
    assert out_lines[out_end + 1 :] == in_lines[key_index + 1 :]

    return out_lines, out_lines[out_begin - 1]


def _inline_document(key_line: str, *, with_markers: bool, body: str = "  - name: old\n") -> str:
    if not with_markers:
        return f"a: 1\n{key_line}\nb: 2\n"
    return f"a: 1\n{key_line}\n{MANAGED_BEGIN}\n{body}{MANAGED_END}\nb: 2\n"


@pytest.mark.parametrize("key_line", INLINE_KEY_LINES)
@pytest.mark.parametrize("with_markers", [False, True], ids=["insert", "replace"])
def test_inline_models_key_parses_after_replace(key_line, with_markers):
    text = _inline_document(key_line, with_markers=with_markers)

    out = replace_managed_section(text, SAMPLE_MODELS)

    parsed = yaml.safe_load(out)
    assert parsed["models"] == SAMPLE_MODELS
    assert parsed["a"] == 1 and parsed["b"] == 2


@pytest.mark.parametrize("key_line", INLINE_VALUE_CASES)
def test_inline_value_is_discarded_and_comment_preserved(key_line):
    text = f"a: 1\n{key_line}\nb: 2\n"

    out_lines, key_out = _assert_only_models_key_line_changed(text, SAMPLE_MODELS)

    assert key_out.strip() == "models:" or key_out.strip().startswith("models: #"), key_out
    assert _has_inline_value(key_out) is False, f"inline value survived: {key_out!r}"
    if "#" in key_line:
        assert "# hi" in key_out, "trailing comment was dropped"
        assert key_out.index("# hi") > key_out.index("models:")
    assert out_lines.count("models:\n") + out_lines.count("models: # hi\n") >= 1


@pytest.mark.parametrize("key_line", INLINE_KEY_LINES)
def test_inline_key_case_outside_region_is_byte_identical(key_line):
    text = f"a: 1\n{key_line}\nb: 2\n"

    out_lines = replace_managed_section(text, SAMPLE_MODELS).splitlines(keepends=True)

    # The only permitted change is the key line itself.
    in_lines = text.splitlines(keepends=True)
    assert out_lines[0] == in_lines[0]  # "a: 1"
    assert out_lines[-1] == in_lines[-1]  # "b: 2"
    assert out_lines[-2].strip() == MANAGED_END
    assert out_lines[1].strip().startswith("models:")


@pytest.mark.parametrize("key_line", INLINE_KEY_LINES)
def test_inline_key_case_comment_survives_on_bare_key(key_line):
    text = f"models:{key_line.split('models:', 1)[1]}\n"

    out_lines = replace_managed_section(text, SAMPLE_MODELS).splitlines(keepends=True)

    prefix = out_lines[0]
    if "# hi" in key_line:
        assert "# hi" in prefix
        assert prefix.strip() != "models:"  # comment kept on the key line
    else:
        assert prefix.rstrip() == "models:"


def test_inline_empty_list_renders_empty_region_and_parses():
    text = "a: 1\nmodels: []\nb: 2\n"

    out = replace_managed_section(text, [])

    parsed = yaml.safe_load(out)
    assert parsed["a"] == 1 and parsed["b"] == 2
    assert "models" not in parsed or parsed["models"] is None
    assert MANAGED_BEGIN in out and MANAGED_END in out


def test_inline_value_with_crlf_keeps_crlf_and_parses():
    text = "a: 1\r\nmodels: []  # hi\r\nb: 2\r\n"

    out = replace_managed_section(text, SAMPLE_MODELS)

    assert out.count("\n") == out.count("\r\n")
    assert "# hi" in out
    parsed = yaml.safe_load(out)
    assert parsed["models"] == SAMPLE_MODELS
    assert parsed["a"] == 1 and parsed["b"] == 2


def test_inline_value_on_unterminated_last_line_parses():
    text = "a: 1\nmodels: []"

    out = replace_managed_section(text, SAMPLE_MODELS)

    parsed = yaml.safe_load(out)
    assert parsed["a"] == 1
    assert parsed["models"] == SAMPLE_MODELS


def test_quoted_hash_in_inline_value_is_not_treated_as_comment():
    text = 'a: 1\nmodels: ["#not-a-comment"]\nb: 2\n'

    out = replace_managed_section(text, SAMPLE_MODELS)

    assert "#not-a-comment" not in out  # value discarded
    assert yaml.safe_load(out)["models"] == SAMPLE_MODELS


# --------------------------------------------------------------------------- #
# Model entries: load from the managed region
# --------------------------------------------------------------------------- #

#: Backups are named `config.yaml.bak.<YYYYMMDD-HHMMSS>`, with a `-N` suffix only
#: when two commits land in the same second.
BACKUP_NAME_RE = re.compile(r"^config\.yaml\.bak\.\d{8}-\d{6}(-\d+)?$")

#: A minimal config that `AppConfig.from_file` accepts: `sandbox` and `models`
#: are required for model validation, everything else has a default.
VALID_MODELS = [
    {
        "name": "m1",
        "display_name": "Model One",
        "use": "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
        "model": "doubao-seed-1-8-251228",
        "api_key": "$TEST_MODEL_KEY",
    }
]


def _config_text(entries: list[dict]) -> str:
    """A minimal, AppConfig-valid config.yaml carrying a marked models region."""
    return f"""\
log_level: info

sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider

models:
{"".join(render_managed_section(entries))}"""


def _listing(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


def _stray_work_files(directory: Path) -> list[str]:
    """Any leftover staging file — the protocol must clean up on every path."""
    return sorted(name for name in _listing(directory) if name.endswith((".work", ".tmp")))


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A directory holding a config.yaml plus an isolated extensions config.

    ``AppConfig.from_file`` also loads ``extensions_config.json`` from the repo
    root; pointing that lookup at an empty temp file keeps these tests
    independent of the developer's gitignored local copy.
    """
    extensions = tmp_path / "extensions_config.json"
    extensions.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions))
    monkeypatch.setenv("TEST_MODEL_KEY", "sk-test-value")

    config = tmp_path / "config.yaml"
    config.write_text(_config_text(VALID_MODELS), encoding="utf-8")
    return tmp_path


def test_load_managed_models_round_trips_replace(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(replace_managed_section(SAMPLE_TEXT, SAMPLE_MODELS), encoding="utf-8")

    assert load_managed_models(config) == SAMPLE_MODELS


def test_empty_when_thinking_disabled_survives_the_round_trip(tmp_path: Path):
    """`when_thinking_disabled: {}` must persist as `{}`, never as `None` or absent.

    The empty block is how the UI says "this endpoint rejects a disable signal,
    so send nothing when thinking is off" without adding a config field. It is
    load-bearing that the writer keeps it (``yaml.safe_dump`` renders `{}`) and
    that the reader hands it back as `{}` rather than dropping the key — a
    `None` would be indistinguishable from "no disable block stored", and
    ``create_chat_model`` would then re-synthesise the disable payload the
    endpoint just refused.
    """
    entry = {
        "name": "claude-proxy",
        "use": "langchain_anthropic:ChatAnthropic",
        "model": "claude-sonnet-4-5",
        "supports_thinking": True,
        "when_thinking_enabled": {"thinking": {"type": "enabled", "budget_tokens": 4096}},
        "when_thinking_disabled": {},
    }
    config = tmp_path / "config.yaml"
    config.write_text(replace_managed_section(SAMPLE_TEXT, [entry]), encoding="utf-8")

    # Rendered as an explicit `{}` — not omitted, not `null`.
    assert "when_thinking_disabled: {}" in config.read_text(encoding="utf-8")

    loaded = load_managed_models(config)[0]
    assert "when_thinking_disabled" in loaded
    assert loaded["when_thinking_disabled"] == {}
    assert loaded["when_thinking_disabled"] is not None
    # And `to_public`, the read path an edit form goes through.
    assert to_public(loaded)["when_thinking_disabled"] == {}


def test_load_managed_models_ignores_commented_templates_without_markers(tmp_path: Path):
    """SAMPLE_TEXT's ``models:`` body is all comments — an empty value, not entries."""
    config = tmp_path / "config.yaml"
    config.write_text(SAMPLE_TEXT, encoding="utf-8")

    assert load_managed_models(config) == []


def test_load_managed_models_reads_real_entries_without_markers(tmp_path: Path):
    """A hand-written config has no markers; its entries must still be visible.

    Returning nothing here is what emptied the admin model list on a deployment
    whose ``config.yaml`` was written by hand: the list showed zero models while
    the models themselves were live.
    """
    config = tmp_path / "config.yaml"
    config.write_text(UNMARKED_WITH_ENTRIES, encoding="utf-8")

    assert [entry["name"] for entry in load_managed_models(config)] == ["existing"]
    assert load_managed_models(config)[0]["api_key"] == "sk-existing"


def test_load_reports_exactly_what_replace_takes_over(tmp_path: Path):
    """The read/write symmetry that keeps a save from dropping live entries.

    A save writes only what the UI submitted, so every entry ``load_managed_models``
    withholds is an entry the next save deletes. Feeding a load straight back into
    a replace — the shape of "open settings, save nothing, close" — must therefore
    be a no-op; before this test existed it silently emptied the section.
    """
    config = tmp_path / "config.yaml"
    config.write_text(UNMARKED_WITH_ENTRIES, encoding="utf-8")
    reported = load_managed_models(config)

    config.write_text(replace_managed_section(UNMARKED_WITH_ENTRIES, reported), encoding="utf-8")

    assert [entry["name"] for entry in reported] == ["existing"]
    assert load_managed_models(config) == reported


def test_load_managed_models_reads_an_inline_flow_sequence(tmp_path: Path):
    """``models: [...]`` on the key line is a value; replace drops it, so load reads it."""
    config = tmp_path / "config.yaml"
    config.write_text("models: [{name: a, use: x:Y, model: m}]\n", encoding="utf-8")

    assert load_managed_models(config) == [{"name": "a", "use": "x:Y", "model": "m"}]


def test_load_managed_models_treats_an_empty_inline_value_as_no_entries(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text("models: []\n", encoding="utf-8")

    assert load_managed_models(config) == []


def test_load_managed_models_returns_empty_without_a_models_section(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text("log_level: info\n", encoding="utf-8")

    assert load_managed_models(config) == []


def test_load_managed_models_rejects_a_non_list_unmarked_section(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text("models:\n  name: not-a-list\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_managed_models(config)


def test_load_managed_models_returns_empty_for_empty_region(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(replace_managed_section(SAMPLE_TEXT, []), encoding="utf-8")

    assert load_managed_models(config) == []


def test_load_managed_models_returns_empty_when_only_one_marker_present(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(f"models:\n  {MANAGED_BEGIN}\n  - name: a\n", encoding="utf-8")

    assert load_managed_models(config) == []


def test_load_managed_models_ignores_the_comment_lines_around_it(tmp_path: Path):
    """The real file is mostly comments; parsing only the region must survive it."""
    if not CONFIG_EXAMPLE.exists():  # pragma: no cover - repo layout guard
        pytest.skip("config.example.yaml not found")

    original = CONFIG_EXAMPLE.read_text(encoding="utf-8")
    assert original.count("#") > 1000, "expected a comment-heavy real config"

    config = tmp_path / "config.yaml"
    config.write_text(replace_managed_section(original, SAMPLE_MODELS), encoding="utf-8")

    assert load_managed_models(config) == SAMPLE_MODELS


def test_load_managed_models_keeps_unicode_and_nested_mappings(tmp_path: Path):
    models = [
        {
            "name": "doubao",
            "display_name": "豆包 · Seed",
            "use": "x:Y",
            "model": "m",
            "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
            "supports_vision": True,
        }
    ]
    config = tmp_path / "config.yaml"
    config.write_text(replace_managed_section(SAMPLE_TEXT, models), encoding="utf-8")

    assert load_managed_models(config) == models


def test_load_managed_models_reads_comments_inside_the_region(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"models:\n  {MANAGED_BEGIN}\n  # operator note\n  - name: a\n    use: x:Y\n    model: m\n  {MANAGED_END}\n",
        encoding="utf-8",
    )

    assert load_managed_models(config) == [{"name": "a", "use": "x:Y", "model": "m"}]


def test_load_managed_models_rejects_malformed_region(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(f"models:\n  {MANAGED_BEGIN}\n  - name: [oops\n  {MANAGED_END}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_managed_models(config)


def test_load_managed_models_rejects_non_list_region(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(f"models:\n  {MANAGED_BEGIN}\n  name: not-a-list\n  {MANAGED_END}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_managed_models(config)


# --------------------------------------------------------------------------- #
# to_public — masking before the model config reaches a browser
# --------------------------------------------------------------------------- #


def test_to_public_leaves_env_reference_untouched():
    """``$VOLCENGINE_API_KEY`` is a pointer, not a secret."""
    public = to_public({"name": "m", "api_key": "$VOLCENGINE_API_KEY"})

    assert public["api_key"] == "$VOLCENGINE_API_KEY"


def test_to_public_masks_literal_key_and_hides_its_middle():
    public = to_public({"name": "m", "api_key": "sk-abcdefgh1234"})

    assert public["api_key"] == "sk-****1234"
    assert "abcdefgh" not in json.dumps(public)


def test_to_public_fully_masks_values_too_short_to_partially_mask():
    for value in ("sk", "ab", "1234567"):
        public = to_public({"api_key": value})

        assert public["api_key"] != value
        assert set(public["api_key"]) == {"*"}, public


def test_to_public_masks_long_values_without_leaking_the_middle():
    public = to_public({"api_key": "sk-verylongsecretvalue-9tail"})

    assert "verylongsecretvalue" not in json.dumps(public)
    assert public["api_key"].startswith("sk-")
    assert public["api_key"].endswith("tail")


def test_to_public_is_a_noop_without_api_key():
    model = {"name": "m", "use": "x:Y", "model": "g"}

    assert to_public(model) == model


def test_to_public_passes_through_non_string_api_keys():
    assert to_public({"api_key": None}) == {"api_key": None}


def test_to_public_returns_a_deep_copy():
    model = {"api_key": "sk-abcdefgh1234", "when_thinking_enabled": {"nested": {"x": 1}}}

    public = to_public(model)

    assert model["api_key"] == "sk-abcdefgh1234"
    public["when_thinking_enabled"]["nested"]["x"] = 2
    assert model["when_thinking_enabled"]["nested"]["x"] == 1


# --------------------------------------------------------------------------- #
# build_model_entry
# --------------------------------------------------------------------------- #


def test_build_model_entry_rejects_missing_use():
    with pytest.raises(ValueError, match=r"required field\(s\): use"):
        build_model_entry({"name": "m", "model": "gpt-4"})


def test_build_model_entry_rejects_missing_model():
    with pytest.raises(ValueError, match=r"required field\(s\): model"):
        build_model_entry({"name": "m", "use": "langchain_openai:ChatOpenAI"})


def test_build_model_entry_lists_every_missing_required_field():
    with pytest.raises(ValueError, match=r"required field\(s\): use, model"):
        build_model_entry({"name": "m"})


def test_build_model_entry_treats_blank_required_fields_as_missing():
    with pytest.raises(ValueError, match=r"required field\(s\): use"):
        build_model_entry({"name": "m", "use": "   ", "model": "gpt-4"})


def test_build_model_entry_rejects_non_mapping_payload():
    with pytest.raises(ValueError):
        build_model_entry(["not", "a", "mapping"])


def test_build_model_entry_defaults_name_to_model():
    entry = build_model_entry({"use": "langchain_openai:ChatOpenAI", "model": "gpt-4"})

    assert entry["name"] == "gpt-4"


def test_build_model_entry_passes_optional_fields_through():
    payload = {
        "name": "m1",
        "use": "langchain_openai:ChatOpenAI",
        "model": "gpt-4",
        "api_key": "$TEST_MODEL_KEY",
        "supports_vision": True,
        "max_tokens": 4096,
        "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
    }

    entry = build_model_entry(payload)

    assert set(entry) == set(payload)
    assert entry["supports_vision"] is True
    assert entry["max_tokens"] == 4096
    assert entry["when_thinking_enabled"] == {"extra_body": {"thinking": {"type": "enabled"}}}


def test_build_model_entry_does_not_mutate_the_payload():
    payload = {"use": "x:Y", "model": "g"}

    build_model_entry(payload)

    assert payload == {"use": "x:Y", "model": "g"}


def test_build_model_entry_rejects_wrongly_typed_fields():
    with pytest.raises(ValueError):
        build_model_entry({"use": "x:Y", "model": "g", "when_thinking_enabled": ["not", "a", "mapping"]})


def test_build_model_entry_round_trips_through_validation(config_dir: Path):
    """build -> render -> validate is the exact path the write endpoint takes."""
    entry = build_model_entry({"name": "m1", "use": "deerflow.models.patched_deepseek:PatchedChatDeepSeek", "model": "doubao-seed-1-8-251228", "api_key": "$TEST_MODEL_KEY", "supports_vision": True})

    parsed = validate_candidate_text(_config_text([entry]), dir_path=config_dir)

    assert [model.name for model in parsed.models] == ["m1"]
    assert parsed.models[0].use == "deerflow.models.patched_deepseek:PatchedChatDeepSeek"
    assert parsed.models[0].supports_vision is True


# --------------------------------------------------------------------------- #
# validate_candidate_text — the candidate is parsed from a throwaway file
# --------------------------------------------------------------------------- #


def test_validate_candidate_text_returns_the_parsed_config(config_dir: Path):
    before = _listing(config_dir)

    parsed = validate_candidate_text(_config_text(VALID_MODELS), dir_path=config_dir)

    assert isinstance(parsed, AppConfig)
    assert [model.name for model in parsed.models] == ["m1"]
    assert _listing(config_dir) == before, "validation must not leave a temp file behind"


def test_validate_candidate_text_parses_the_given_text(config_dir: Path):
    other = [{**VALID_MODELS[0], "name": "renamed"}]

    parsed = validate_candidate_text(_config_text(other), dir_path=config_dir)

    assert [model.name for model in parsed.models] == ["renamed"]


def test_validate_candidate_text_rejects_unresolvable_env_var(config_dir: Path):
    before = _listing(config_dir)
    broken = [{**VALID_MODELS[0], "api_key": "$TEST_MISSING_MODEL_KEY"}]

    with pytest.raises(ValueError, match="TEST_MISSING_MODEL_KEY"):
        validate_candidate_text(_config_text(broken), dir_path=config_dir)

    assert _listing(config_dir) == before, "temp file must be removed on the failure path"


def test_validate_candidate_text_rejects_malformed_yaml(config_dir: Path):
    before = _listing(config_dir)

    with pytest.raises(ValueError):
        validate_candidate_text("models:\n  - name: [oops\n", dir_path=config_dir)

    assert _listing(config_dir) == before


def test_validate_candidate_text_rejects_invalid_model_entry(config_dir: Path):
    before = _listing(config_dir)
    missing_use = [{"name": "m", "model": "gpt-4"}]

    with pytest.raises(ValueError):
        validate_candidate_text(_config_text(missing_use), dir_path=config_dir)

    assert _listing(config_dir) == before


def test_validate_candidate_text_writes_the_candidate_inside_dir_path(config_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Relative paths inside config.yaml resolve against the config's directory."""
    seen: list[tuple[Path, bool]] = []
    real_from_file = AppConfig.from_file

    def spy(cls, config_path=None):  # noqa: ANN001, ANN202 - mirrors the classmethod signature
        path = Path(config_path)
        # Existence has to be sampled inside the call: the protocol removes the
        # candidate before it returns, so a later check would always be False.
        seen.append((path, path.exists()))
        return real_from_file(config_path)

    monkeypatch.setattr(AppConfig, "from_file", classmethod(spy))

    validate_candidate_text(_config_text(VALID_MODELS), dir_path=config_dir)

    assert len(seen) == 1
    assert seen[0][0].parent == config_dir
    assert seen[0][1], "the candidate must exist while it is being parsed"


def test_validate_candidate_text_overwrites_nothing_in_the_config_dir(config_dir: Path):
    config = config_dir / "config.yaml"
    untouched = config.read_text(encoding="utf-8")

    validate_candidate_text(_config_text([{**VALID_MODELS[0], "name": "other"}]), dir_path=config_dir)

    assert config.read_text(encoding="utf-8") == untouched


# --------------------------------------------------------------------------- #
# commit_config_update — steps 4-5 of the write protocol
# --------------------------------------------------------------------------- #


def test_commit_config_update_replaces_the_file_and_returns_the_backup(tmp_path: Path):
    config = tmp_path / "config.yaml"
    original = "log_level: info\nmodels:\n"
    config.write_text(original, encoding="utf-8")
    new_text = original + "  # added\n"

    backup = commit_config_update(config, new_text)

    assert config.read_text(encoding="utf-8") == new_text
    assert backup.parent == tmp_path
    assert BACKUP_NAME_RE.match(backup.name), backup.name
    assert backup.read_text(encoding="utf-8") == original, "the backup must hold the pre-replace original"
    assert _stray_work_files(tmp_path) == []
    assert _listing(tmp_path) == sorted(["config.yaml", backup.name])


def test_two_successive_commits_produce_two_distinct_backups(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text("v1\n", encoding="utf-8")

    first = commit_config_update(config, "v2\n")
    second = commit_config_update(config, "v3\n")

    assert first != second, "a fixed backup name would clobber the earlier backup"
    assert first.read_text(encoding="utf-8") == "v1\n"
    assert second.read_text(encoding="utf-8") == "v2\n"
    assert config.read_text(encoding="utf-8") == "v3\n"


def test_commit_config_update_preserves_bytes_outside_the_written_text(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_bytes(b"# comment\r\nlog_level: info\r\n")
    new_text = "# comment\r\nlog_level: debug\r\n"

    commit_config_update(config, new_text)

    assert config.read_bytes() == new_text.encode("utf-8")


def test_backup_failure_leaves_config_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = tmp_path / "config.yaml"
    original = b"log_level: info\nmodels:\n"
    config.write_bytes(original)
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):  # noqa: ANN001, ANN202 - mirrors builtins.open
        if ".bak." in str(file):
            raise OSError(28, "No space left on device")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    with pytest.raises(OSError):
        commit_config_update(config, "log_level: debug\n")

    assert config.read_bytes() == original
    assert _listing(tmp_path) == ["config.yaml"], "a failed commit must not leave staging files behind"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory write permissions")
def test_backup_failure_in_read_only_dir_leaves_config_untouched(tmp_path: Path):
    config = tmp_path / "config.yaml"
    original = "log_level: info\n"
    config.write_text(original, encoding="utf-8")

    os.chmod(tmp_path, 0o500)
    try:
        with pytest.raises(OSError):
            commit_config_update(config, "log_level: debug\n")
        assert config.read_text(encoding="utf-8") == original
    finally:
        os.chmod(tmp_path, 0o700)

    assert _listing(tmp_path) == ["config.yaml"]


def test_replace_failure_leaves_config_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = tmp_path / "config.yaml"
    original = b"log_level: info\nmodels:\n"
    config.write_bytes(original)

    def failing_replace(src, dst, *args, **kwargs):  # noqa: ANN001, ANN202 - mirrors os.replace
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        commit_config_update(config, "log_level: debug\n")

    assert config.read_bytes() == original
    assert _listing(tmp_path) == ["config.yaml"], "a failed commit must not leave staging files behind"


def test_commit_config_update_requires_an_existing_config(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        commit_config_update(tmp_path / "config.yaml", "log_level: info\n")


def test_staging_failure_leaves_config_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The live file is only replaced after the staged copy lands."""
    config = tmp_path / "config.yaml"
    original = b"log_level: info\nmodels:\n"
    config.write_bytes(original)
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):  # noqa: ANN001, ANN202 - mirrors builtins.open
        if str(file).endswith(".work"):
            raise OSError(28, "No space left on device")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    with pytest.raises(OSError):
        commit_config_update(config, "log_level: debug\n")

    assert config.read_bytes() == original
    assert _listing(tmp_path) == ["config.yaml"], "the staged copy must not survive the failure"


def test_prune_failure_after_the_replace_keeps_the_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A failed prune must not roll back a commit that already landed.

    The backup is the only copy of the pre-commit original once ``os.replace``
    has run, so a prune error must leave both the new file and that backup in
    place instead of unwinding the write.
    """
    config = tmp_path / "config.yaml"
    config.write_text("v1\n", encoding="utf-8")

    def failing_prune(config_path, keep=DEFAULT_BACKUP_KEEP):  # noqa: ANN001, ANN202 - mirrors the real signature
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(models_section, "prune_backups", failing_prune)

    backup = commit_config_update(config, "v2\n")

    assert config.read_text(encoding="utf-8") == "v2\n"
    assert backup.read_text(encoding="utf-8") == "v1\n", "the rollback copy must survive a prune failure"


def test_commit_config_update_accepts_an_injectable_clock(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text("v1\n", encoding="utf-8")

    backup = commit_config_update(config, "v2\n", now=datetime(2026, 9, 12, 14, 30, 0))

    assert backup.name == "config.yaml.bak.20260912-143000"
    assert backup.read_text(encoding="utf-8") == "v1\n"


# --------------------------------------------------------------------------- #
# prune_backups — keep the last N timestamped backups
# --------------------------------------------------------------------------- #


def _make_backup(directory: Path, stamp: str, *, name: str = "config.yaml", content: str | None = None) -> Path:
    """Create a backup file whose mtime agrees with its timestamped name.

    *stamp* may carry a same-second ``-N`` suffix; only the leading
    ``YYYYMMDD-HHMMSS`` is a strptime-able timestamp.
    """
    path = directory / f"{name}.bak.{stamp}"
    path.write_text(content if content is not None else f"backup {stamp}\n", encoding="utf-8")
    moment = datetime.strptime(stamp[:15], "%Y%m%d-%H%M%S").timestamp()
    os.utime(path, (moment, moment))
    return path


def test_prune_backups_deletes_the_oldest_beyond_keep(tmp_path: Path):
    live = tmp_path / "config.yaml"
    live.write_text("live\n", encoding="utf-8")
    moments = [datetime(2026, 1, 1) + timedelta(seconds=index) for index in range(12)]
    stamps = [moment.strftime("%Y%m%d-%H%M%S") for moment in moments]
    # Created newest first, so passing this test requires real ordering.
    for stamp in reversed(stamps):
        _make_backup(tmp_path, stamp)

    deleted = prune_backups(live, keep=10)

    assert sorted(path.name for path in deleted) == [f"config.yaml.bak.{stamps[0]}", f"config.yaml.bak.{stamps[1]}"]
    assert sorted(path.name for path in tmp_path.glob("config.yaml.bak.*")) == [f"config.yaml.bak.{stamps[index]}" for index in range(2, 12)]
    assert live.read_text(encoding="utf-8") == "live\n"


def test_prune_backups_keeps_everything_when_under_keep(tmp_path: Path):
    live = tmp_path / "config.yaml"
    live.write_text("live\n", encoding="utf-8")
    backups = [_make_backup(tmp_path, f"2026010{index + 1}-000000") for index in range(3)]

    assert prune_backups(live, keep=10) == []

    assert sorted(tmp_path.glob("config.yaml.bak.*")) == sorted(backups)


def test_prune_backups_deletes_everything_when_keep_is_zero(tmp_path: Path):
    live = tmp_path / "config.yaml"
    live.write_text("live\n", encoding="utf-8")
    _make_backup(tmp_path, "20260101-000000")
    _make_backup(tmp_path, "20260102-000000")

    deleted = prune_backups(live, keep=0)

    assert len(deleted) == 2
    assert list(tmp_path.glob("config.yaml.bak.*")) == []
    assert live.exists()


def test_prune_backups_never_deletes_unrelated_files(tmp_path: Path):
    live = tmp_path / "config.yaml"
    live.write_text("live\n", encoding="utf-8")
    unrelated = []
    for name in ("notes.txt", "config.yaml.bak", "config.yaml.backup", "config.yaml.tmp"):
        path = tmp_path / name
        path.write_text("keep me\n", encoding="utf-8")
        unrelated.append(path)
    unrelated.append(_make_backup(tmp_path, "20260101-000000", name="other.yaml"))
    _make_backup(tmp_path, "20260101-120000")
    _make_backup(tmp_path, "20260102-000000")

    deleted = prune_backups(live, keep=1)

    assert [path.name for path in deleted] == ["config.yaml.bak.20260101-120000"]
    assert all(path.exists() for path in unrelated), "a non-backup file was deleted"
    assert live.read_text(encoding="utf-8") == "live\n"


def test_prune_backups_orders_same_second_backups(tmp_path: Path):
    """Two commits inside one second get `-N` suffixes; newest still wins."""
    live = tmp_path / "config.yaml"
    live.write_text("live\n", encoding="utf-8")
    _make_backup(tmp_path, "20260101-120000")
    _make_backup(tmp_path, "20260101-120000-1")
    _make_backup(tmp_path, "20260101-120000-2")

    deleted = prune_backups(live, keep=1)

    assert [path.name for path in deleted] == ["config.yaml.bak.20260101-120000", "config.yaml.bak.20260101-120000-1"]
    assert [path.name for path in tmp_path.glob("config.yaml.bak.*")] == ["config.yaml.bak.20260101-120000-2"]


def test_commit_config_update_prunes_to_the_keep_limit(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text("v0\n", encoding="utf-8")

    backups = [commit_config_update(config, f"v{index + 1}\n") for index in range(12)]

    assert len(backups) == len(set(backups)), "every commit must produce its own backup"
    remaining = sorted(path.name for path in tmp_path.glob("config.yaml.bak.*"))
    assert len(remaining) == 10
    assert backups[-1].name in remaining, "the newest backup must survive pruning"
    assert config.read_text(encoding="utf-8") == "v12\n"
    assert _stray_work_files(tmp_path) == []


# --------------------------------------------------------------------------- #
# upsert_env_var — writing .env and syncing the running process
#
# `load_dotenv()` is called once, at import (`app_config.py:44`), so a key that
# lands in `.env` after startup is invisible to the running process: `$NEW_KEY`
# resolves to None, `resolve_env_variables` raises, and — since config reload has
# no exception guard — every later request fails. Writing the file is therefore
# never enough on its own; `os.environ` must be updated by the same call.
#
# The write order matters too. Validation of a candidate config happens *before*
# the config is swapped in, so `.env` has to be written first or adding a model
# that references a new key can never be saved at all. The accepted consequence
# is that a failure after step 1 leaves an unreferenced key behind in `.env`;
# that residue is harmless and is deliberately not rolled back (rolling back
# could clobber values the operator typed by hand).
# --------------------------------------------------------------------------- #

#: `.env` keys these tests may write into the process environment.
ENV_TEST_KEYS = (
    "OPENAI_API_KEY",
    "VOLCENGINE_API_KEY",
    "EMPTY",
    "EXPORTED",
    "APPENDED_KEY",
    "SPACED_KEY",
    "HASH_KEY",
    "QUOTED_KEY",
    "DUPLICATE_KEY",
    "NEW_MODEL_KEY",
    "REPLACED_KEY",
)

#: A `.env` shaped like one an operator would hand-edit: comments, blank lines,
#: an exported entry, a quoted value, and a trailing empty value.
ENV_SAMPLE = """\
# DeerFlow environment
OPENAI_API_KEY=sk-existing

# a comment
VOLCENGINE_API_KEY="quoted value"
EMPTY=
export EXPORTED=hello
OPENAI_API_KEY_EXTRA=untouched
"""


@pytest.fixture(autouse=True)
def _clean_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the keys these tests write out of the process environment.

    ``upsert_env_var`` sets ``os.environ`` on purpose, so a leak would be visible
    to every later test in the suite (and a stray ``OPENAI_API_KEY`` could change
    unrelated behaviour). ``monkeypatch`` restores the original value — or
    absence — once the test finishes.
    """
    for key in ENV_TEST_KEYS:
        monkeypatch.delenv(key, raising=False)


def _env_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _assert_only_env_line_changed(before: str, after: str, *, key: str) -> int:
    """Assert every ``.env`` line except *key*'s is byte-identical.

    Returns the index of the rewritten line. This is the same guarantee the
    config editor makes for ``config.yaml``, applied to the dotenv file: only the
    addressed line may move.
    """
    in_lines = before.splitlines(keepends=True)
    out_lines = after.splitlines(keepends=True)
    index = next(i for i, line in enumerate(in_lines) if line.startswith(f"{key}="))

    assert out_lines[:index] == in_lines[:index], "lines before the rewritten key changed"
    assert out_lines[index + 1 :] == in_lines[index + 1 :], "lines after the rewritten key changed"
    assert len(out_lines) == len(in_lines), "no line may be added or removed by an in-place rewrite"
    return index


def test_upsert_env_var_replaces_existing_key_in_place(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(ENV_SAMPLE, encoding="utf-8")

    upsert_env_var(env_path, "OPENAI_API_KEY", "sk-new")

    after = env_path.read_text(encoding="utf-8")
    index = _assert_only_env_line_changed(ENV_SAMPLE, after, key="OPENAI_API_KEY")
    assert after.splitlines(keepends=True)[index] == "OPENAI_API_KEY=sk-new\n"


def test_upsert_env_var_replaces_the_whole_line_not_just_the_value(tmp_path: Path):
    """No stale quoting survives: ``"quoted value"`` is rewritten as a bare value."""
    env_path = tmp_path / ".env"
    env_path.write_text(ENV_SAMPLE, encoding="utf-8")

    upsert_env_var(env_path, "VOLCENGINE_API_KEY", "plain")

    line = next(line for line in _env_lines(env_path) if line.startswith("VOLCENGINE_API_KEY="))
    assert line == "VOLCENGINE_API_KEY=plain\n"


def test_upsert_env_var_appends_a_missing_key(tmp_path: Path):
    env_path = tmp_path / ".env"
    before = "APPENDED_KEY_NEIGHBOUR=1\n"
    env_path.write_text(before, encoding="utf-8")

    upsert_env_var(env_path, "APPENDED_KEY", "2")

    assert env_path.read_text(encoding="utf-8") == before + "APPENDED_KEY=2\n"


def test_upsert_env_var_terminates_a_file_without_a_trailing_newline(tmp_path: Path):
    """The one documented exception: an unterminated last line gets a newline.

    Appending directly would produce ``A=1APPENDED_KEY=2``, which dotenv reads as
    a single bogus entry.
    """
    env_path = tmp_path / ".env"
    env_path.write_text("A=1", encoding="utf-8")

    upsert_env_var(env_path, "APPENDED_KEY", "2")

    assert env_path.read_text(encoding="utf-8") == "A=1\nAPPENDED_KEY=2\n"


def test_upsert_env_var_preserves_crlf_line_endings(tmp_path: Path):
    """The Windows writer emits CRLF; the editor must not mix in lone LFs.

    Read as bytes on purpose: ``Path.read_text`` translates newlines and would
    hide a CRLF file being silently converted to LF.
    """
    env_path = tmp_path / ".env"
    before = ENV_SAMPLE.replace("\n", "\r\n")
    env_path.write_bytes(before.encode("utf-8"))

    upsert_env_var(env_path, "OPENAI_API_KEY", "sk-new")

    after_bytes = env_path.read_bytes()
    assert after_bytes.count(b"\n") == after_bytes.count(b"\r\n"), "a lone LF was introduced"
    assert b"OPENAI_API_KEY=sk-new\r\n" in after_bytes
    assert after_bytes.startswith(b"# DeerFlow environment\r\n")


def test_upsert_env_var_appends_with_crlf_for_a_crlf_file(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"A=1\r\n")

    upsert_env_var(env_path, "APPENDED_KEY", "2")

    assert env_path.read_bytes() == b"A=1\r\nAPPENDED_KEY=2\r\n"


def test_upsert_env_var_keeps_an_lf_file_as_lf(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"A=1\n")

    upsert_env_var(env_path, "APPENDED_KEY", "2")

    assert env_path.read_bytes() == b"A=1\nAPPENDED_KEY=2\n"


def test_upsert_env_var_is_idempotent(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(ENV_SAMPLE, encoding="utf-8")

    upsert_env_var(env_path, "SPACED_KEY", "abc def")
    once = env_path.read_text(encoding="utf-8")
    upsert_env_var(env_path, "SPACED_KEY", "abc def")

    assert env_path.read_text(encoding="utf-8") == once, "a second identical write must be a no-op"
    assert sum(line.startswith("SPACED_KEY=") for line in once.splitlines()) == 1


def test_upsert_env_var_is_idempotent_for_an_existing_key(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(ENV_SAMPLE, encoding="utf-8")

    upsert_env_var(env_path, "OPENAI_API_KEY", "sk-new")
    once = env_path.read_text(encoding="utf-8")
    upsert_env_var(env_path, "OPENAI_API_KEY", "sk-new")

    assert env_path.read_text(encoding="utf-8") == once
    assert sum(line.startswith("OPENAI_API_KEY=") for line in once.splitlines()) == 1


def test_upsert_env_var_rewrites_the_last_duplicate_assignment(tmp_path: Path):
    """dotenv keeps the *last* assignment, so that is the one that must win.

    A hand-edited file can carry the same key twice; rewriting the first would
    leave the stale value in charge after the next restart.
    """
    env_path = tmp_path / ".env"
    before = "DUPLICATE_KEY=old\n# keep me\nDUPLICATE_KEY=stale\n"
    env_path.write_text(before, encoding="utf-8")

    upsert_env_var(env_path, "DUPLICATE_KEY", "fresh")

    after = env_path.read_text(encoding="utf-8")
    assert after == "DUPLICATE_KEY=old\n# keep me\nDUPLICATE_KEY=fresh\n"
    assert dotenv_values(env_path)["DUPLICATE_KEY"] == "fresh"


def test_upsert_env_var_syncs_the_running_process(tmp_path: Path):
    """The whole point of the task: the running process must see the new key."""
    env_path = tmp_path / ".env"
    assert "SPACED_KEY" not in os.environ, "test isolation fixture failed to clear the key"

    upsert_env_var(env_path, "SPACED_KEY", "sk-live-value")

    assert os.environ["SPACED_KEY"] == "sk-live-value"


def test_upsert_env_var_overwrites_a_stale_process_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``os.environ`` is overwritten, not merely filled when absent.

    ``load_dotenv()`` never overrides an existing variable, so a stale in-process
    value would otherwise shadow the key the operator just saved.
    """
    monkeypatch.setenv("SPACED_KEY", "stale")
    env_path = tmp_path / ".env"
    env_path.write_text("SPACED_KEY=stale\n", encoding="utf-8")

    upsert_env_var(env_path, "SPACED_KEY", "fresh")

    assert os.environ["SPACED_KEY"] == "fresh"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SPACED_KEY", "abc def"),
        ("HASH_KEY", "abc#def"),
        ("HASH_KEY", "abc #def"),
        ("QUOTED_KEY", ""),
        ("QUOTED_KEY", "sk-plain"),
    ],
)
def test_upsert_env_var_round_trips_values_through_dotenv(tmp_path: Path, key: str, value: str):
    env_path = tmp_path / ".env"

    upsert_env_var(env_path, key, value)

    raw = next(line for line in _env_lines(env_path) if line.startswith(f"{key}="))
    if value == "" or re.search(r"[\s#]", value):
        assert raw[len(key) + 1] in "\"'", f"a value needing quotes was written bare: {raw!r}"
    assert dotenv_values(env_path)[key] == value


def test_upsert_env_var_quotes_like_write_dotenv(tmp_path: Path):
    """Quoting rules mirror ``Write-DotEnv`` in DeerFlow.Common.psm1."""
    env_path = tmp_path / ".env"

    upsert_env_var(env_path, "SPACED_KEY", "abc def")
    upsert_env_var(env_path, "HASH_KEY", "abc#def")
    upsert_env_var(env_path, "QUOTED_KEY", 'has "quotes" and a space')

    lines = dict(line.rstrip("\r\n").split("=", 1) for line in _env_lines(env_path))
    assert lines["SPACED_KEY"] == '"abc def"'
    assert lines["HASH_KEY"] == '"abc#def"'
    # A value containing a double quote is single-quoted; double quotes would
    # mangle backslashes, so they are never used when the value has one.
    assert lines["QUOTED_KEY"] == "'has \"quotes\" and a space'"


def test_upsert_env_var_prefers_single_quotes_for_backslash_values(tmp_path: Path):
    """Double quotes would turn ``\\note`` into a newline."""
    env_path = tmp_path / ".env"

    upsert_env_var(env_path, "SPACED_KEY", "C:\\dir name\\note")

    assert dotenv_values(env_path)["SPACED_KEY"] == "C:\\dir name\\note"


def test_upsert_env_var_preserves_the_existing_file_mode(tmp_path: Path):
    """Rewriting must not silently tighten permissions on a shared .env."""
    env_path = tmp_path / ".env"
    env_path.write_text("A=1\n", encoding="utf-8")
    os.chmod(env_path, 0o644)

    upsert_env_var(env_path, "APPENDED_KEY", "2")

    assert (env_path.stat().st_mode & 0o777) == 0o644


def test_upsert_env_var_preserves_unrelated_content(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(ENV_SAMPLE, encoding="utf-8")

    upsert_env_var(env_path, "APPENDED_KEY", "new")

    after = env_path.read_text(encoding="utf-8")
    preserved = (
        "# DeerFlow environment\n",
        "# a comment\n",
        "\n",
        'VOLCENGINE_API_KEY="quoted value"\n',
        "EMPTY=\n",
        "export EXPORTED=hello\n",
        "OPENAI_API_KEY_EXTRA=untouched\n",
    )
    for line in preserved:
        assert line in after, f"{line!r} was lost"
    assert after.startswith("# DeerFlow environment\n")
    assert after.endswith("APPENDED_KEY=new\n")


def test_upsert_env_var_creates_the_file_and_parent_directories(tmp_path: Path):
    env_path = tmp_path / "src" / "nested" / ".env"
    assert not env_path.parent.exists()

    upsert_env_var(env_path, "APPENDED_KEY", "value")

    assert env_path.read_text(encoding="utf-8") == "APPENDED_KEY=value\n"
    # No BOM: python-dotenv would read "KEY" as a different key name.
    assert not env_path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_upsert_env_var_creates_a_utf8_file(tmp_path: Path):
    env_path = tmp_path / "src" / ".env"

    upsert_env_var(env_path, "APPENDED_KEY", "值 with space")

    assert dotenv_values(env_path)["APPENDED_KEY"] == "值 with space"


def test_upsert_env_var_rejects_an_invalid_key(tmp_path: Path):
    env_path = tmp_path / ".env"

    with pytest.raises(ValueError):
        upsert_env_var(env_path, "NOT A KEY", "v")

    assert not env_path.exists(), "a rejected key must not create the file"


def test_upsert_env_var_rejects_a_multiline_value(tmp_path: Path):
    """A value with a newline cannot be represented on one line; refuse it."""
    env_path = tmp_path / ".env"

    with pytest.raises(ValueError):
        upsert_env_var(env_path, "APPENDED_KEY", "line1\nline2")  # noqa: SIM905 - a newline is the point

    assert not env_path.exists()


def test_upsert_env_var_leaves_no_staging_file_behind(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("A=1\n", encoding="utf-8")

    upsert_env_var(env_path, "APPENDED_KEY", "2")

    assert _listing(tmp_path) == [".env"]


# --------------------------------------------------------------------------- #
# The ordering constraint: .env before validation
# --------------------------------------------------------------------------- #


def test_new_env_key_must_land_before_the_candidate_is_validated(config_dir: Path):
    """End-to-end regression for the ordering constraint.

    ``validate_candidate_text`` runs *before* the config is swapped in, and
    ``resolve_env_variables`` is a hard failure on an unresolvable ``$VAR``. So
    the ``.env`` write has to come first — otherwise adding a model that
    references a new key could never be saved. The first half below proves the
    failure is real; the second half proves the ordering fixes it.
    """
    env_path = config_dir / "src" / ".env"
    new_entry = {"name": "x", "use": "langchain_openai:ChatOpenAI", "model": "gpt-4o", "api_key": "$NEW_MODEL_KEY"}

    candidate = replace_managed_section(_config_text(VALID_MODELS), [new_entry])

    # Before the .env write the key cannot resolve — this is the bug this task
    # exists to prevent.
    with pytest.raises(ValueError, match="NEW_MODEL_KEY"):
        validate_candidate_text(candidate, dir_path=config_dir)

    upsert_env_var(env_path, "NEW_MODEL_KEY", "sk-test-123")

    parsed = validate_candidate_text(candidate, dir_path=config_dir)
    assert [model.name for model in parsed.models] == ["x"]
    assert parsed.models[0].api_key == "sk-test-123"


def test_failed_validation_leaves_the_env_key_in_place(config_dir: Path):
    """The accepted residue, pinned: `.env` keeps the new key when config.yaml cannot be written.

    An unreferenced environment variable changes nothing about how the app runs,
    and rolling one back risks clobbering values the operator typed by hand —
    which is a far worse failure than a stray unused key.
    """
    env_path = config_dir / "src" / ".env"
    upsert_env_var(env_path, "NEW_MODEL_KEY", "sk-test-123")

    with pytest.raises(ValueError):
        validate_candidate_text("models:\n  - name: [oops\n", dir_path=config_dir)

    assert dotenv_values(env_path)["NEW_MODEL_KEY"] == "sk-test-123"
    assert os.environ["NEW_MODEL_KEY"] == "sk-test-123"


# --------------------------------------------------------------------------- #
# Line-ending preservation (regression: read_text() universal-newline translation)
# --------------------------------------------------------------------------- #


def test_read_config_text_preserves_crlf(tmp_path: Path):
    """`Path.read_text()` would normalise CRLF -> LF; `read_config_text` must not.

    This is the read-side half of the line-ending guarantee. The deployment
    config is 100% CRLF, so a lossy read makes every line of the committed file
    differ even though the edit itself is region-scoped.
    """
    path = tmp_path / "crlf.yaml"
    path.write_bytes(b"a: 1\r\nmodels:\r\n  - name: x\r\nb: 2\r\n")

    assert read_config_text(path) == "a: 1\r\nmodels:\r\n  - name: x\r\nb: 2\r\n"


def test_replace_managed_section_round_trips_a_crlf_file(tmp_path: Path):
    """A CRLF file stays CRLF end to end, and only the region changes."""
    original = "a: 1\r\nmodels:\r\n  - name: old\r\n    use: p:Q\r\n    model: m\r\nb: 2\r\n"
    out = replace_managed_section(original, [{"name": "new", "use": "p:Q", "model": "m"}])

    assert "\n" in out
    assert out.count("\r\n") == out.count("\n"), "every newline must be part of a CRLF pair"
    assert "\r\nmodels:\r\n" in out
    # Outside the region is byte-identical, CRLF included.
    assert out.startswith("a: 1\r\nmodels:\r\n")
    assert out.endswith("b: 2\r\n")


def test_detect_newline_majority_beats_first_line():
    """One stray LF must not flip a CRLF file to LF."""
    lines = ["head\n"] + [f"x{i}\r\n" for i in range(9)]
    assert _detect_newline(lines) == "\r\n"


def test_detect_newline_all_lf():
    assert _detect_newline(["a\n", "b\n"]) == "\n"


def test_detect_newline_empty():
    assert _detect_newline([]) == "\n"


def test_load_managed_models_preserves_crlf(tmp_path: Path):
    """The read helper is used for loading too, so load must not mangle endings."""
    path = tmp_path / "config.yaml"
    body = f"models:\r\n  {MANAGED_BEGIN}\r\n  - name: x\r\n    use: p:Q\r\n    model: m\r\n  {MANAGED_END}\r\n"
    path.write_bytes(body.encode("utf-8"))

    assert [m["name"] for m in load_managed_models(path)] == ["x"]
    # Untouched on disk.
    assert path.read_bytes() == body.encode("utf-8")
