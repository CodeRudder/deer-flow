import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.gateway.admin.quota_service import QuotaService
from app.gateway.deps import get_config, require_admin_user
from deerflow.config.app_config import AppConfig
from deerflow.config.models_section import build_model_entry, commit_config_update, load_managed_models, replace_managed_section, to_public, upsert_env_var, validate_candidate_text
from deerflow.models.image_generation import ImageGenerationProvidersResponse, get_image_generation_providers
from deerflow.models.video_generation import VideoGenerationModel, VideoGenerationProvider, VideoGenerationProvidersResponse, get_video_generation_providers
from deerflow.persistence.engine import get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["models"])

_ADMIN_REQUIRED_DETAIL = "Admin privileges required to manage model configuration."


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


class VideoResolutionRate(BaseModel):
    """单个分辨率的视频积分费率（元/秒区间；无按秒覆盖时 min == max）。"""

    resolution: str = Field(..., description="Resolution name as configured in the billing rules")
    yuan_per_second_min: float = Field(..., description="Lowest CNY-per-second rate for this resolution")
    yuan_per_second_max: float = Field(..., description="Highest CNY-per-second rate for this resolution")


class VideoModelBilling(BaseModel):
    """用户侧价目摘要（由管理员费率规则推导，key 为小写模型名）。"""

    resolutions: list[VideoResolutionRate] = Field(default_factory=list)
    min_duration_seconds: int | None = Field(None, description="Minimum supported duration across resolutions")
    max_duration_seconds: int | None = Field(None, description="Maximum supported duration across resolutions")
    regeneration_yuan_per_second: float | None = Field(None, description="CNY-per-second rate for regeneration (upscale) calls; None when not configured")


class VideoGenerationModelWithBilling(VideoGenerationModel):
    billing: VideoModelBilling | None = Field(None, description="User-facing rate summary; None when no video quota scope is configured")


class VideoGenerationProviderWithBilling(VideoGenerationProvider):
    models: list[VideoGenerationModelWithBilling] = Field(default_factory=list)


class VideoGenerationProvidersWithBilling(VideoGenerationProvidersResponse):
    providers: list[VideoGenerationProviderWithBilling] = Field(default_factory=list)


def _attach_video_billing(
    response: VideoGenerationProvidersResponse,
    summary: dict | None,
) -> VideoGenerationProvidersWithBilling:
    """按小写模型名把价目摘要合并进 providers 响应；无摘要时 billing 保持 None。"""
    summary = summary or {}
    enriched_providers = []
    for provider in response.providers:
        models = [
            VideoGenerationModelWithBilling(
                **model.model_dump(),
                billing=summary.get(model.name.lower()),
            )
            for model in provider.models
        ]
        provider_data = provider.model_dump()
        provider_data["models"] = models
        enriched_providers.append(VideoGenerationProviderWithBilling(**provider_data))
    return VideoGenerationProvidersWithBilling(skill_enabled=response.skill_enabled, providers=enriched_providers)


@router.get(
    "/video-generation/providers",
    response_model=VideoGenerationProvidersWithBilling,
    tags=["video-generation"],
    summary="List Video Generation Providers",
    description="Retrieve video generation providers exposed by the built-in video-generation skill, enriched with user-facing point rates when a video quota scope is configured.",
)
async def list_video_generation_providers() -> VideoGenerationProvidersWithBilling:
    base = get_video_generation_providers()
    session_factory = get_session_factory()
    summary = None
    if session_factory is not None:
        summary = await QuotaService(session_factory).get_video_billing_summary()
    return _attach_video_billing(base, summary)


@router.get(
    "/image-generation/providers",
    response_model=ImageGenerationProvidersResponse,
    tags=["image-generation"],
    summary="List Image Generation Providers",
    description="Retrieve image generation providers exposed by the built-in image-generation skill.",
)
async def list_image_generation_providers() -> ImageGenerationProvidersResponse:
    return get_image_generation_providers()


# --------------------------------------------------------------------------- #
# UI-managed model configuration (`config.yaml` -> managed `models:` region)
#
# These endpoints edit the marker-delimited region of ``config.yaml`` in place.
# Everything outside it — the ~1471 comment lines documenting operator intent —
# is byte-identical afterwards. The five-step write protocol (env, edit copy,
# validate, backup, atomic replace) lives in ``deerflow.config.models_section``;
# this layer only translates payloads, applies the masked-key rule, and maps
# ``ValueError`` onto HTTP status codes.
# --------------------------------------------------------------------------- #

_MASK = "****"
#: Response-only metadata for an entry, never written back to ``config.yaml``.
_ENTRY_META_KEYS = ("index", "api_key_masked")
#: Payload-only field: the cleartext to store under a ``$VAR`` api_key reference.
_API_KEY_VALUE_FIELD = "api_key_value"


class ManagedModelEntry(BaseModel):
    """A model entry as the UI round-trips it.

    ``extra="allow"`` keeps provider-specific fields (``api_base``,
    ``when_thinking_enabled``, ``max_tokens``, ...) intact; ``ModelConfig`` is
    what actually validates the shape.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(None, description="Unique name; defaults to the provider model id")
    model: str | None = Field(None, description="Actual provider model identifier")
    use: str | None = Field(None, description="Class path of the model provider")
    api_key: str | None = Field(None, description="Literal key, or a $VAR reference; masked in responses")
    api_key_value: str | None = Field(None, description="Cleartext to persist under a $VAR api_key; write-only, never stored in config.yaml")
    api_key_masked: bool | None = Field(None, description="Response-only: whether api_key shown is a mask")


class ManagedModelResponse(BaseModel):
    """A managed entry plus the addressing metadata the UI needs.

    Fields are echoed from the file rather than re-declared: ``extra="allow"``
    keeps provider-specific keys intact and avoids inventing ``null`` slots for
    a field an entry simply does not set (and keeps the write-only
    ``api_key_value`` off the wire).
    """

    model_config = ConfigDict(extra="allow")

    index: int = Field(..., description="Position of the entry inside the managed region")
    api_key_masked: bool = Field(default=False, description="True when api_key is a mask, not the stored value")


class ModelConfigListResponse(BaseModel):
    """The whole managed region."""

    models: list[ManagedModelResponse]


class ModelConfigReplaceRequest(BaseModel):
    """Body of ``PUT /api/models/config`` — the full replacement list."""

    models: list[ManagedModelEntry]


def _resolve_config_path() -> Path:
    """Resolve the live ``config.yaml`` or fail with an actionable 500."""
    try:
        return AppConfig.resolve_config_path()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _public_entry(model: dict, index: int) -> ManagedModelResponse:
    """Mask *model* for the browser and attach its managed-region index."""
    public = to_public(dict(model))
    stored_key = model.get("api_key")
    return ManagedModelResponse(
        **public,
        index=index,
        api_key_masked=isinstance(stored_key, str) and stored_key != public.get("api_key"),
    )


def _list_managed(config_path: Path) -> list[ManagedModelResponse]:
    return [_public_entry(model, index) for index, model in enumerate(load_managed_models(config_path))]


def _to_stored_entries(payloads: list[dict], existing: list[dict], *, config_path: Path) -> list[dict]:
    """Turn UI payloads into storable entries, preserving stored secrets.

    Two payload-only concerns are handled here: an ``api_key`` that is absent,
    empty, or the mask the UI was just shown means "leave the stored value
    alone" (clobbering it with ``sk-****1234`` would silently break the model);
    and ``api_key_value`` is the cleartext behind a new ``$VAR`` reference, which
    is written to ``.env`` instead of into ``config.yaml``.
    """
    stored_by_name: dict[str, dict] = {}
    for entry in existing:
        name = entry.get("name")
        if isinstance(name, str):
            stored_by_name[name] = entry

    return [_to_stored_entry(payload, stored_by_name, config_path=config_path) for payload in payloads]


def _to_stored_entry(payload: dict, stored_by_name: dict[str, dict], *, config_path: Path) -> dict:
    entry = build_model_entry(payload)
    name = entry["name"]
    entry.pop(_API_KEY_VALUE_FIELD, None)
    # The round-tripped response carries addressing metadata; it describes the
    # entry's position in a response, not the model, so it never lands in the file.
    for meta_key in _ENTRY_META_KEYS:
        entry.pop(meta_key, None)

    existing = stored_by_name.get(name)
    api_key = entry.get("api_key")
    if isinstance(api_key, str) and api_key.startswith("$"):
        secret = payload.get(_API_KEY_VALUE_FIELD)
        if isinstance(secret, str) and secret:
            upsert_env_var(config_path.parent / ".env", api_key[1:], secret)
        return entry

    if not api_key or (isinstance(api_key, str) and _MASK in api_key):
        # A mask or an empty value is not a credential: keep whatever is stored
        # under this name, and store nothing at all when there is nothing to keep.
        stored_key = existing.get("api_key") if existing is not None else None
        if stored_key is None:
            entry.pop("api_key", None)
        else:
            entry["api_key"] = stored_key
    return entry


def _write_managed_models(config_path: Path, models: list[dict]) -> Path:
    """Run the fixed write protocol and return the backup path.

    Order is load-bearing and must not be reordered:

    1. ``upsert_env_var`` — a new ``$VAR`` must exist in ``os.environ`` before
       validation, because ``load_dotenv()`` only runs once at import, so a key
       written to ``.env`` alone is invisible and ``$NEW_KEY`` would resolve to
       ``None`` and fail every save.
    2. ``replace_managed_section`` — a surgical edit of the copy's region.
    3. ``validate_candidate_text`` — raises on a bad candidate; the original has
       not been touched at this point, so a rejection costs nothing and leaves
       no backup litter.
    4+5. ``commit_config_update`` — timestamped backup, then one atomic replace.
    """
    text = config_path.read_text(encoding="utf-8")
    candidate = replace_managed_section(text, models)
    validate_candidate_text(candidate, dir_path=config_path.parent)
    return commit_config_update(config_path, candidate)


def _reject(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=f"Invalid model configuration: {exc}")


@router.get(
    "/models/config",
    response_model=ModelConfigListResponse,
    summary="Get Managed Model Configuration",
    description="List the models in the UI-managed region of config.yaml (admin only). Each entry carries its index and name so the UI can address it; literal api_keys are masked.",
)
async def get_models_config(request: Request) -> ModelConfigListResponse:
    """Return the managed ``models:`` region with secrets masked."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    return ModelConfigListResponse(models=_list_managed(_resolve_config_path()))


@router.put(
    "/models/config",
    response_model=ModelConfigListResponse,
    summary="Replace Managed Model Configuration",
    description="Replace the entire UI-managed `models:` region with the posted list (admin only). A masked or empty api_key keeps the stored value for an entry of the same name.",
)
async def replace_models_config(request: Request, body: ModelConfigReplaceRequest) -> ModelConfigListResponse:
    """Replace the whole managed region."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    config_path = _resolve_config_path()
    existing = load_managed_models(config_path)
    try:
        models = _to_stored_entries([entry.model_dump(exclude_none=True) for entry in body.models], existing, config_path=config_path)
        _write_managed_models(config_path, models)
    except ValueError as exc:
        raise _reject(exc) from exc
    logger.info("Replaced %d managed model(s) in %s", len(models), config_path)
    return ModelConfigListResponse(models=_list_managed(config_path))


@router.post(
    "/models",
    response_model=ModelConfigListResponse,
    summary="Add Managed Model",
    description="Append one model entry to the UI-managed region of config.yaml (admin only). Returns 409 when the name is already taken.",
)
async def create_managed_model(request: Request, entry: ManagedModelEntry) -> ModelConfigListResponse:
    """Append a single entry to the managed region."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    config_path = _resolve_config_path()
    existing = load_managed_models(config_path)
    try:
        stored = _to_stored_entry(entry.model_dump(exclude_none=True), {}, config_path=config_path)
        if any(model.get("name") == stored["name"] for model in existing):
            raise HTTPException(status_code=409, detail=f"Model '{stored['name']}' already exists")
        _write_managed_models(config_path, [*existing, stored])
    except ValueError as exc:
        raise _reject(exc) from exc
    logger.info("Added managed model %s to %s", stored["name"], config_path)
    return ModelConfigListResponse(models=_list_managed(config_path))


@router.put(
    "/models/{model_name}",
    response_model=ModelConfigListResponse,
    summary="Update Managed Model",
    description="Replace one entry in the UI-managed region by name (admin only). Returns 404 when the name is not managed; a masked or empty api_key keeps the stored value.",
)
async def update_managed_model(request: Request, model_name: str, entry: ManagedModelEntry) -> ModelConfigListResponse:
    """Replace one managed entry, keeping its position."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    config_path = _resolve_config_path()
    existing = load_managed_models(config_path)
    index = next((i for i, model in enumerate(existing) if model.get("name") == model_name), None)
    if index is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found in managed configuration")
    try:
        payload = entry.model_dump(exclude_none=True)
        payload["name"] = model_name
        stored = _to_stored_entry(payload, {model_name: existing[index]}, config_path=config_path)
        models = list(existing)
        models[index] = stored
        _write_managed_models(config_path, models)
    except ValueError as exc:
        raise _reject(exc) from exc
    logger.info("Updated managed model %s in %s", model_name, config_path)
    return ModelConfigListResponse(models=_list_managed(config_path))


@router.delete(
    "/models/{model_name}",
    response_model=ModelConfigListResponse,
    summary="Delete Managed Model",
    description="Remove one entry from the UI-managed region of config.yaml by name (admin only). Returns 404 when the name is not managed.",
)
async def delete_managed_model(request: Request, model_name: str) -> ModelConfigListResponse:
    """Remove one managed entry."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    config_path = _resolve_config_path()
    existing = load_managed_models(config_path)
    remaining = [model for model in existing if model.get("name") != model_name]
    if len(remaining) == len(existing):
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found in managed configuration")
    try:
        _write_managed_models(config_path, remaining)
    except ValueError as exc:
        raise _reject(exc) from exc
    logger.info("Deleted managed model %s from %s", model_name, config_path)
    return ModelConfigListResponse(models=_list_managed(config_path))


# Declared after `/models/config` on purpose: FastAPI matches routes in
# registration order, so this greedy `{model_name}` path must not come first or
# it would swallow `GET /api/models/config`.
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
