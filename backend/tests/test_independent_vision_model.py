from __future__ import annotations

import pytest

from deerflow.agents.lead_agent import agent as lead_agent_module
from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.config.vision_model_config import VisionModelConfig
from deerflow.tools.tools import get_available_tools


def _model(name: str = "text-model", *, supports_vision: bool = False) -> ModelConfig:
    return ModelConfig(
        name=name,
        display_name=name,
        description=None,
        use="langchain_openai:ChatOpenAI",
        model=name,
        supports_vision=supports_vision,
    )


def _vision_model(name: str = "company-vision") -> VisionModelConfig:
    return VisionModelConfig(
        name=name,
        model="kimi-k2.6",
        base_url="https://vision.example.test/api/v1/messages",
        api_key="test-key",
    )


def _app_config(*, main_supports_vision: bool = False, vision_models: list[VisionModelConfig] | None = None) -> AppConfig:
    return AppConfig(
        models=[_model(supports_vision=main_supports_vision)],
        vision_models=vision_models or [],
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
    )


def test_app_config_rejects_multiple_vision_models() -> None:
    with pytest.raises(ValueError, match="Only one entry"):
        _app_config(vision_models=[_vision_model("vision-a"), _vision_model("vision-b")])


def test_app_config_rejects_streaming_vision_model() -> None:
    with pytest.raises(ValueError, match="stream"):
        _app_config(
            vision_models=[
                VisionModelConfig(
                    name="company-vision",
                    model="kimi-k2.6",
                    base_url="https://vision.example.test/api/v1/messages",
                    stream=True,
                )
            ]
        )


def test_get_available_tools_exposes_view_image_for_independent_vision_model(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _app_config(main_supports_vision=False, vision_models=[_vision_model()])
    monkeypatch.setattr("deerflow.tools.tools.is_host_bash_allowed", lambda config: True)

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False, app_config=config)]

    assert "view_image" in names


def test_get_available_tools_omits_view_image_without_any_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _app_config(main_supports_vision=False)
    monkeypatch.setattr("deerflow.tools.tools.is_host_bash_allowed", lambda config: True)

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False, app_config=config)]

    assert "view_image" not in names


def test_lead_middlewares_skip_view_image_middleware_for_independent_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _app_config(main_supports_vision=True, vision_models=[_vision_model()])
    monkeypatch.setattr(lead_agent_module, "build_lead_runtime_middlewares", lambda *, app_config, lazy_init=True: [])
    monkeypatch.setattr(lead_agent_module, "_create_summarization_middleware", lambda **kwargs: None)
    monkeypatch.setattr(lead_agent_module, "_create_todo_list_middleware", lambda is_plan_mode: None)

    middlewares = lead_agent_module.build_middlewares(
        {"configurable": {"is_plan_mode": False, "subagent_enabled": False}},
        model_name="text-model",
        app_config=config,
    )

    assert not any(isinstance(middleware, ViewImageMiddleware) for middleware in middlewares)


def test_subagent_middlewares_skip_view_image_middleware_for_independent_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _app_config(main_supports_vision=True, vision_models=[_vision_model()])
    monkeypatch.setattr(
        "deerflow.agents.middlewares.tool_error_handling_middleware._build_runtime_middlewares",
        lambda **kwargs: [],
    )

    middlewares = build_subagent_runtime_middlewares(app_config=config, model_name="text-model")

    assert not any(isinstance(middleware, ViewImageMiddleware) for middleware in middlewares)


def test_subagent_middlewares_keep_legacy_view_image_middleware_without_independent_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _app_config(main_supports_vision=True)
    monkeypatch.setattr(
        "deerflow.agents.middlewares.tool_error_handling_middleware._build_runtime_middlewares",
        lambda **kwargs: [],
    )

    middlewares = build_subagent_runtime_middlewares(app_config=config, model_name="text-model")

    assert any(isinstance(middleware, ViewImageMiddleware) for middleware in middlewares)
