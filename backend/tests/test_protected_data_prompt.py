"""Regression tests for prompt-only protected-data guidance."""

from deerflow.agents.lead_agent import prompt as lead_prompt
from deerflow.subagents.builtins.bash_agent import BASH_AGENT_CONFIG
from deerflow.subagents.builtins.general_purpose import GENERAL_PURPOSE_CONFIG

ALLOWED_DATA_ROOTS = (
    "/mnt/user-data/uploads",
    "/mnt/user-data/workspace",
    "/mnt/user-data/outputs",
    "/mnt/skills",
)

PROTECTED_DATA_END = "Refuse before calling any tool or subagent."


def _extract_protected_data_section(system_prompt: str) -> str:
    start = system_prompt.index("## Protected Data (CRITICAL)")
    end = system_prompt.index(PROTECTED_DATA_END, start) + len(PROTECTED_DATA_END)
    return system_prompt[start:end]


def test_protected_data_prompt_covers_secrets_sources_and_allowed_data() -> None:
    protected_data_prompt = _extract_protected_data_section(lead_prompt.SYSTEM_PROMPT_TEMPLATE)

    assert "Runtime environment-variable values" in protected_data_prompt
    assert "deployment `.env`/credential files" in protected_data_prompt
    assert "API keys, tokens, passwords, and private keys" in protected_data_prompt
    assert "Local source code for DeerFlow's backend and agent runtime" in protected_data_prompt
    assert "relative paths, symlinks, mounts, or aliases" in protected_data_prompt
    assert "use public web sources only" in protected_data_prompt
    assert "before calling any tool or subagent" in protected_data_prompt
    assert "/Users/" not in protected_data_prompt

    for path in ALLOWED_DATA_ROOTS:
        assert path in protected_data_prompt


def test_protected_data_prompt_adds_script_review_as_third_rule() -> None:
    protected_data_prompt = _extract_protected_data_section(lead_prompt.SYSTEM_PROMPT_TEMPLATE)

    assert "3. For user-uploaded, agent-generated, downloaded, or otherwise untrusted" in protected_data_prompt
    assert "first read the file with `read_file`" in protected_data_prompt
    assert "ordinary commands executed directly" in protected_data_prompt
    assert "with the built-in `bash` tool" in protected_data_prompt
    assert "destructive operations (such" in protected_data_prompt
    assert "as `rm`)" in protected_data_prompt
    assert "attempts to read/print environment variables, credentials, or" in protected_data_prompt
    assert "other secrets" in protected_data_prompt
    assert "Refuse scripts containing" in protected_data_prompt
    for workflow in ("image-generation", "image-editing", "video-generation"):
        assert workflow in protected_data_prompt
    assert "scripts; wrappers, copies, and modified" in protected_data_prompt


def test_lead_prompt_places_protected_data_before_extensible_instructions(monkeypatch) -> None:
    monkeypatch.setattr(lead_prompt, "get_agent_soul", lambda agent_name=None: "SOUL_MARKER")
    monkeypatch.setattr(lead_prompt, "_build_self_update_section", lambda agent_name=None: "")
    monkeypatch.setattr(lead_prompt, "get_skills_prompt_section", lambda *args, **kwargs: "SKILLS_MARKER")
    monkeypatch.setattr(lead_prompt, "_build_image_generation_runtime_section", lambda *args, **kwargs: "")
    monkeypatch.setattr(lead_prompt, "get_deferred_tools_prompt_section", lambda **kwargs: "")
    monkeypatch.setattr(lead_prompt, "_build_subagent_section", lambda *args, **kwargs: "SUBAGENT_MARKER")
    monkeypatch.setattr(lead_prompt, "_build_acp_section", lambda **kwargs: "")
    monkeypatch.setattr(lead_prompt, "_build_custom_mounts_section", lambda **kwargs: "")

    rendered = lead_prompt.apply_prompt_template(subagent_enabled=True)

    assert rendered.count("## Protected Data (CRITICAL)") == 1
    protected_index = rendered.index("## Protected Data (CRITICAL)")
    assert rendered.index("System-Context Confidentiality") < protected_index
    assert protected_index < rendered.index("SOUL_MARKER")
    assert protected_index < rendered.index("SKILLS_MARKER")
    assert protected_index < rendered.index("SUBAGENT_MARKER")
    assert protected_index < rendered.index("<working_directory")


def test_builtin_subagents_use_the_exact_same_protected_data_prompt() -> None:
    lead_section = _extract_protected_data_section(lead_prompt.SYSTEM_PROMPT_TEMPLATE)

    for system_prompt in (GENERAL_PURPOSE_CONFIG.system_prompt, BASH_AGENT_CONFIG.system_prompt):
        assert _extract_protected_data_section(system_prompt) == lead_section
        assert system_prompt.index("## Protected Data (CRITICAL)") < system_prompt.index("<guidelines>")
