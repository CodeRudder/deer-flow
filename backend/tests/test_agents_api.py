"""Unit tests for the custom-agent management API router.

Focus: ``PUT /api/agents/{name}`` model-name validation (422 on unknown model)
and the temp+rename atomic write behavior introduced with the agent edit UI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.gateway.routers.agents import AgentUpdateRequest, update_agent
from deerflow.config.agents_api_config import load_agents_api_config_from_dict
from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
from deerflow.config.model_config import ModelConfig
from deerflow.config.paths import get_paths
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.runtime.user_context import get_effective_user_id

pytestmark = pytest.mark.asyncio


def _make_app_config() -> AppConfig:
    return AppConfig(
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
        models=[
            ModelConfig(name="gpt-test", display_name="GPT Test", description=None, use="langchain_openai:ChatOpenAI", model="gpt-test"),
            ModelConfig(name="claude-test", display_name="Claude Test", description=None, use="langchain_openai:ChatOpenAI", model="claude-test"),
        ],
    )


def _seed_agent(name: str, *, model: str | None = None, skills: list[str] | None = None) -> Path:
    agent_dir = get_paths().user_agent_dir(get_effective_user_id(), name)
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_lines = [f"name: {name}", "description: seeded"]
    if model is not None:
        config_lines.append(f"model: {model}")
    if skills is not None:
        config_lines.append("skills:")
        config_lines.extend(f"  - {s}" for s in skills)
    (agent_dir / "config.yaml").write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    (agent_dir / "SOUL.md").write_text("Original soul.\n", encoding="utf-8")
    return agent_dir


async def test_update_agent_writes_model_description_and_soul(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    set_app_config(_make_app_config())
    load_agents_api_config_from_dict({"enabled": True})
    try:
        agent_dir = _seed_agent("edit-ok", model="gpt-test")

        response = await update_agent(
            "edit-ok",
            AgentUpdateRequest(model="claude-test", description="updated", soul="New soul.\n"),
        )

        assert response.model == "claude-test"
        assert response.description == "updated"
        # The API strips soul content (load_agent_soul); disk keeps the exact bytes.
        assert response.soul == "New soul."
        assert "model: claude-test" in (agent_dir / "config.yaml").read_text(encoding="utf-8")
        assert (agent_dir / "SOUL.md").read_text(encoding="utf-8") == "New soul.\n"
    finally:
        load_agents_api_config_from_dict({})
        reset_app_config()


async def test_update_agent_rejects_unknown_model_with_422(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    set_app_config(_make_app_config())
    load_agents_api_config_from_dict({"enabled": True})
    try:
        agent_dir = _seed_agent("edit-bad-model", model="gpt-test")

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await update_agent("edit-bad-model", AgentUpdateRequest(model="no-such-model"))

        assert exc_info.value.status_code == 422
        # Nothing was written: config and soul keep their original values.
        assert "model: gpt-test" in (agent_dir / "config.yaml").read_text(encoding="utf-8")
        assert (agent_dir / "SOUL.md").read_text(encoding="utf-8") == "Original soul.\n"
    finally:
        load_agents_api_config_from_dict({})
        reset_app_config()


async def test_update_agent_without_model_field_skips_validation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    set_app_config(_make_app_config())
    load_agents_api_config_from_dict({"enabled": True})
    try:
        agent_dir = _seed_agent("edit-no-model", model="gpt-test")

        await update_agent("edit-no-model", AgentUpdateRequest(soul="Rewritten.\n"))

        assert "model: gpt-test" in (agent_dir / "config.yaml").read_text(encoding="utf-8")
        assert (agent_dir / "SOUL.md").read_text(encoding="utf-8") == "Rewritten.\n"
    finally:
        load_agents_api_config_from_dict({})
        reset_app_config()


async def test_update_agent_keeps_unmentioned_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    set_app_config(_make_app_config())
    load_agents_api_config_from_dict({"enabled": True})
    try:
        agent_dir = _seed_agent("edit-keep-fields", model="gpt-test", skills=["search"])

        await update_agent("edit-keep-fields", AgentUpdateRequest(description="only description changed"))

        config_text = (agent_dir / "config.yaml").read_text(encoding="utf-8")
        assert "model: gpt-test" in config_text
        assert "- search" in config_text
        assert "description: only description changed" in config_text
    finally:
        load_agents_api_config_from_dict({})
        reset_app_config()


async def test_update_agent_leaves_no_temp_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    set_app_config(_make_app_config())
    load_agents_api_config_from_dict({"enabled": True})
    try:
        agent_dir = _seed_agent("edit-clean", model="gpt-test")

        await update_agent("edit-clean", AgentUpdateRequest(model="claude-test", soul="Clean.\n"))

        leftovers = [p.name for p in agent_dir.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []
    finally:
        load_agents_api_config_from_dict({})
        reset_app_config()


async def test_update_agent_model_set_to_null_clears_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    set_app_config(_make_app_config())
    load_agents_api_config_from_dict({"enabled": True})
    try:
        agent_dir = _seed_agent("edit-null-model", model="gpt-test")

        response = await update_agent("edit-null-model", AgentUpdateRequest(model=None))

        assert response.model is None
        assert "model:" not in (agent_dir / "config.yaml").read_text(encoding="utf-8")
    finally:
        load_agents_api_config_from_dict({})
        reset_app_config()
