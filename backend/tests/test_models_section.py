"""Tests for the surgical ``models:`` section editor.

The editor must never reserialize config.yaml: the file is 80% comments that
document operator intent, and ``yaml.dump`` would erase them. Every assertion
about "byte-identical outside the managed region" below is the whole point of
the module — a destructive rewrite would silently delete ~1471 comment lines.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from deerflow.config.models_section import (
    MANAGED_BEGIN,
    MANAGED_END,
    find_models_block,
    render_managed_section,
    replace_managed_section,
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
