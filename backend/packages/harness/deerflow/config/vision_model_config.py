from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_VISION_SYSTEM_PROMPT = "你是一个专业的多模态视觉理解模型。请客观、准确、结构化地描述图片内容，提取对后续任务有帮助的信息。不要输出推理过程。"
DEFAULT_VISION_PROMPT = "请理解这张图片的主要内容，提取对后续任务有帮助的信息。"


class VisionModelConfig(BaseModel):
    """Config section for an independent image-understanding model."""

    name: str = Field(..., description="Unique name for the vision model")
    display_name: str | None = Field(default=None, description="Human-readable name for display")
    is_default: bool = Field(default=False, description="Default vision model when none is selected; first match wins if multiple are set.")
    model: str = Field(..., description="Provider model name")
    base_url: str = Field(..., description="Endpoint URL; must match api_style (anthropic → /v1/messages, openai → /v1/chat/completions)")
    api_style: Literal["anthropic", "openai"] = Field(
        default="anthropic",
        description="Request/response protocol. 'anthropic': Messages API (default). 'openai': Chat Completions, forces stream + SSE; ignores the stream field.",
    )
    api_key: str | None = Field(default=None, description="API key for the vision endpoint")
    max_tokens: int = Field(default=10240, description="Maximum output tokens for image understanding")
    stream: bool = Field(default=False, description="Whether to request streaming responses")
    timeout: float = Field(default=60.0, description="HTTP request timeout in seconds")
    headers: dict[str, str] | None = Field(default=None, description="Optional HTTP headers for the vision endpoint")
    model_config = ConfigDict(extra="allow")


class VisionConfig(BaseModel):
    """Shared image-understanding configuration."""

    system_prompt: str = Field(default=DEFAULT_VISION_SYSTEM_PROMPT, description="Top-level system prompt sent with each vision request")
    prompt: str = Field(default=DEFAULT_VISION_PROMPT, description="User prompt sent with each image")
    models: list[VisionModelConfig] = Field(default_factory=list, description="Independent image-understanding models")
    model_config = ConfigDict(extra="allow")


def has_configured_vision_model(config: Any) -> bool:
    """Return True when an AppConfig-like object has an independent vision model."""
    vision = getattr(config, "vision", None)
    vision_models = getattr(vision, "models", None)
    return isinstance(vision_models, list) and len(vision_models) > 0


def get_vision_model_config(config: Any, name: str | None = None) -> VisionModelConfig | None:
    """Return a named or default vision model config, if present."""
    if not has_configured_vision_model(config):
        return None
    if name:
        getter = getattr(config, "get_vision_model_config", None)
        if callable(getter):
            return getter(name)
        return next((model for model in config.vision.models if model.name == name), None)
    return next((model for model in config.vision.models if model.is_default), None) or config.vision.models[0]
