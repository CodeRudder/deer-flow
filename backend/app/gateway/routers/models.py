from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.gateway.deps import get_config
from deerflow.config.app_config import AppConfig
from deerflow.models.image_generation import ImageGenerationProvidersResponse, get_image_generation_providers
from deerflow.models.video_generation import VideoGenerationProvidersResponse, get_video_generation_providers

router = APIRouter(prefix="/api", tags=["models"])


class ModelResponse(BaseModel):
    """Response model for model information."""

    name: str = Field(..., description="Unique identifier for the model")
    model: str = Field(..., description="Actual provider model identifier")
    display_name: str | None = Field(None, description="Human-readable name")
    description: str | None = Field(None, description="Model description")
    supports_thinking: bool = Field(default=False, description="Whether model supports thinking mode")
    supports_reasoning_effort: bool = Field(default=False, description="Whether model supports reasoning effort")


class TokenUsageResponse(BaseModel):
    """Token usage display configuration."""

    enabled: bool = Field(default=False, description="Whether token usage display is enabled")


class VisionModelResponse(BaseModel):
    """Response model for independent vision model information."""

    name: str = Field(..., description="Unique identifier for the vision model")
    model: str = Field(..., description="Actual provider model identifier")
    display_name: str | None = Field(None, description="Human-readable name")
    is_default: bool = Field(default=False, description="Whether this is the default vision model when none is selected")


class ModelsListResponse(BaseModel):
    """Response model for listing all models."""

    models: list[ModelResponse]
    vision_models: list[VisionModelResponse] = Field(default_factory=list)
    token_usage: TokenUsageResponse


@router.get(
    "/models",
    response_model=ModelsListResponse,
    summary="List All Models",
    description="Retrieve a list of all available AI models configured in the system.",
)
async def list_models(config: AppConfig = Depends(get_config)) -> ModelsListResponse:
    """List all available models from configuration.

    Returns model information suitable for frontend display,
    excluding sensitive fields like API keys and internal configuration.

    Returns:
        A list of all configured models with their metadata and token usage display settings.

    Example Response:
        ```json
        {
            "models": [
                {
                    "name": "gpt-4",
                    "model": "gpt-4",
                    "display_name": "GPT-4",
                    "description": "OpenAI GPT-4 model",
                    "supports_thinking": false,
                    "supports_reasoning_effort": false
                },
                {
                    "name": "claude-3-opus",
                    "model": "claude-3-opus",
                    "display_name": "Claude 3 Opus",
                    "description": "Anthropic Claude 3 Opus model",
                    "supports_thinking": true,
                    "supports_reasoning_effort": false
                }
            ],
            "vision_models": [
                {
                    "name": "doubao-vision",
                    "model": "doubao-seed-2.0-pro",
                    "display_name": "Doubao Seed 视觉理解"
                }
            ],
            "token_usage": {
                "enabled": true
            }
        }
        ```
    """
    models = [
        ModelResponse(
            name=model.name,
            model=model.model,
            display_name=model.display_name,
            description=model.description,
            supports_thinking=model.supports_thinking,
            supports_reasoning_effort=model.supports_reasoning_effort,
        )
        for model in config.models
    ]
    vision_models = [
        VisionModelResponse(
            name=model.name,
            model=model.model,
            display_name=model.display_name,
            is_default=model.is_default,
        )
        for model in config.vision.models
    ]
    return ModelsListResponse(
        models=models,
        vision_models=vision_models,
        token_usage=TokenUsageResponse(enabled=config.token_usage.enabled),
    )


@router.get(
    "/models/{model_name}",
    response_model=ModelResponse,
    summary="Get Model Details",
    description="Retrieve detailed information about a specific AI model by its name.",
)
async def get_model(model_name: str, config: AppConfig = Depends(get_config)) -> ModelResponse:
    """Get a specific model by name.

    Args:
        model_name: The unique name of the model to retrieve.

    Returns:
        Model information if found.

    Raises:
        HTTPException: 404 if model not found.

    Example Response:
        ```json
        {
            "name": "gpt-4",
            "display_name": "GPT-4",
            "description": "OpenAI GPT-4 model",
            "supports_thinking": false
        }
        ```
    """
    model = config.get_model_config(model_name)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    return ModelResponse(
        name=model.name,
        model=model.model,
        display_name=model.display_name,
        description=model.description,
        supports_thinking=model.supports_thinking,
        supports_reasoning_effort=model.supports_reasoning_effort,
    )


@router.get(
    "/video-generation/providers",
    response_model=VideoGenerationProvidersResponse,
    tags=["video-generation"],
    summary="List Video Generation Providers",
    description="Retrieve video generation providers exposed by the built-in video-generation skill.",
)
async def list_video_generation_providers() -> VideoGenerationProvidersResponse:
    return get_video_generation_providers()


@router.get(
    "/image-generation/providers",
    response_model=ImageGenerationProvidersResponse,
    tags=["image-generation"],
    summary="List Image Generation Providers",
    description="Retrieve image generation providers exposed by the built-in image-generation skill.",
)
async def list_image_generation_providers() -> ImageGenerationProvidersResponse:
    return get_image_generation_providers()
