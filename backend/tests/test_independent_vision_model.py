from __future__ import annotations

import pytest

from deerflow.agents.lead_agent import agent as lead_agent_module
from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.config.vision_model_config import VisionConfig, VisionModelConfig, get_vision_model_config
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


def _vision_model(name: str = "company-vision", *, is_default: bool = False) -> VisionModelConfig:
    return VisionModelConfig(
        name=name,
        model="kimi-k2.6",
        base_url="https://vision.example.test/api/v1/messages",
        api_key="test-key",
        is_default=is_default,
    )


def _app_config(*, main_supports_vision: bool = False, vision_models: list[VisionModelConfig] | None = None) -> AppConfig:
    return AppConfig(
        models=[_model(supports_vision=main_supports_vision)],
        vision=VisionConfig(models=vision_models or []),
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
    )


def test_app_config_allows_multiple_vision_models() -> None:
    config = _app_config(vision_models=[_vision_model("vision-a"), _vision_model("vision-b")])

    assert [model.name for model in config.vision.models] == ["vision-a", "vision-b"]
    assert config.get_vision_model_config("vision-b") == config.vision.models[1]


def test_app_config_rejects_legacy_top_level_vision_models() -> None:
    with pytest.raises(ValueError, match="vision_models.*vision\\.models"):
        AppConfig.model_validate(
            {
                "models": [_model().model_dump()],
                "vision_models": [_vision_model().model_dump()],
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            }
        )


def test_app_config_rejects_duplicate_vision_model_names() -> None:
    with pytest.raises(ValueError, match="Duplicate vision model name"):
        _app_config(vision_models=[_vision_model("vision-a"), _vision_model("vision-a")])


def test_app_config_allows_streaming_vision_model() -> None:
    config = _app_config(
        vision_models=[
            VisionModelConfig(
                name="company-vision",
                model="kimi-k2.6",
                base_url="https://vision.example.test/api/v1/messages",
                stream=True,
            )
        ]
    )

    assert config.vision.models[0].stream is True


def test_get_available_tools_exposes_view_image_for_independent_vision_model(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _app_config(main_supports_vision=False, vision_models=[_vision_model()])
    monkeypatch.setattr("deerflow.tools.tools.is_host_bash_allowed", lambda config: True)

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False, app_config=config)]

    assert "view_image" in names


def test_get_available_tools_wraps_async_view_image_for_sync_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _app_config(main_supports_vision=False, vision_models=[_vision_model()])
    monkeypatch.setattr("deerflow.tools.tools.is_host_bash_allowed", lambda config: True)

    tools = get_available_tools(include_mcp=False, subagent_enabled=False, app_config=config)
    view_image = next(tool for tool in tools if tool.name == "view_image")

    assert view_image.coroutine is not None
    assert view_image.func is not None


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


def test_get_vision_model_config_uses_is_default_when_unselected() -> None:
    config = _app_config(vision_models=[_vision_model("vision-a"), _vision_model("vision-b", is_default=True)])

    assert get_vision_model_config(config).name == "vision-b"


def test_get_vision_model_config_is_default_first_one_wins() -> None:
    config = _app_config(
        vision_models=[
            _vision_model("vision-a", is_default=True),
            _vision_model("vision-b", is_default=True),
        ]
    )

    assert get_vision_model_config(config).name == "vision-a"


def test_get_vision_model_config_falls_back_to_first_without_is_default() -> None:
    config = _app_config(vision_models=[_vision_model("vision-a"), _vision_model("vision-b")])

    assert get_vision_model_config(config).name == "vision-a"


def test_get_vision_model_config_name_overrides_is_default() -> None:
    config = _app_config(vision_models=[_vision_model("vision-a"), _vision_model("vision-b", is_default=True)])

    assert get_vision_model_config(config, "vision-a").name == "vision-a"


def test_vision_model_api_style_defaults_to_anthropic() -> None:
    config = _app_config(vision_models=[_vision_model()])

    assert config.vision.models[0].api_style == "anthropic"


def test_vision_model_accepts_openai_api_style() -> None:
    model = VisionModelConfig(
        name="gpt-vision",
        model="gpt-5.5",
        base_url="https://vision.example.test/openai/v1/chat/completions",
        api_key="test-key",
        api_style="openai",
    )

    config = _app_config(vision_models=[model])

    assert config.vision.models[0].api_style == "openai"
    # stream keeps its own default; api_style does not override it.
    assert config.vision.models[0].stream is False
