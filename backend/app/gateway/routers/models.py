import asyncio
import logging
import os
import time
from pathlib import Path
from typing import NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.gateway.admin.quota_service import QuotaService
from app.gateway.deps import get_config, require_admin_user
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.config.model_config import ModelConfig
from deerflow.config.models_section import build_model_entry, commit_config_update, load_managed_models, read_config_text, replace_managed_section, to_public, upsert_env_var, validate_candidate_text
from deerflow.models import factory as model_factory
from deerflow.models.image_generation import ImageGenerationProvidersResponse, get_image_generation_providers
from deerflow.models.video_generation import VideoGenerationModel, VideoGenerationProvider, VideoGenerationProvidersResponse, get_video_generation_providers
from deerflow.persistence.engine import get_session_factory
from deerflow.reflection import resolve_class

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


class _ProviderPreset(NamedTuple):
    """One selectable provider in the model form.

    ``use`` is the class path the entry will carry; ``default_api_base`` is the
    endpoint to prefill when the provider has one (``None`` means "the SDK's own
    default" — the form leaves the field empty). Values come from the commented
    examples in ``config.example.yaml``; none are invented here.
    """

    key: str
    label: str
    use: str
    default_api_base: str | None = None


#: The provider dropdown, in display order. Extending the form means adding one
#: row here — the availability probe and the endpoint are generic over the table.
#: `openai-compatible` is the deliberate fallback for a hand-crafted entry whose
#: `use` matches no row: the UI shows that label but keeps the stored class path
#: verbatim, so an advanced configuration survives an edit untouched.
_PROVIDER_PRESETS: tuple[_ProviderPreset, ...] = (
    _ProviderPreset("openai", "OpenAI", "langchain_openai:ChatOpenAI"),
    _ProviderPreset("openai-compatible", "其他 OpenAI 兼容 (OpenAI-compatible)", "langchain_openai:ChatOpenAI"),
    _ProviderPreset("doubao", "豆包 (火山方舟)", "deerflow.models.patched_deepseek:PatchedChatDeepSeek", "https://ark.cn-beijing.volces.com/api/v3"),
    _ProviderPreset("deepseek", "DeepSeek", "deerflow.models.patched_deepseek:PatchedChatDeepSeek", "https://api.deepseek.com/v1"),
    _ProviderPreset("kimi", "Kimi (Moonshot)", "deerflow.models.patched_deepseek:PatchedChatDeepSeek", "https://api.moonshot.cn/v1"),
    _ProviderPreset("minimax", "MiniMax", "deerflow.models.patched_minimax:PatchedChatMiniMax"),
    _ProviderPreset("anthropic", "Anthropic Claude", "langchain_anthropic:ChatAnthropic"),
    _ProviderPreset("google", "Google Gemini", "langchain_google_genai:ChatGoogleGenerativeAI"),
    _ProviderPreset("ollama", "Ollama (本地)", "langchain_ollama:ChatOllama"),
)


class ModelProviderPreset(BaseModel):
    """A provider row plus the result of probing its class path."""

    key: str = Field(..., description="Stable identifier the form stores in its provider field")
    label: str = Field(..., description="Human-readable provider name")
    use: str = Field(..., description="Class path an entry using this provider carries")
    default_api_base: str | None = Field(None, description="Endpoint to prefill; null means the SDK default")
    available: bool = Field(..., description="True when the class path resolves in this process")
    reason: str | None = Field(None, description="Why the class path did not resolve; null when it did")


class ModelProvidersResponse(BaseModel):
    """The provider dropdown."""

    providers: list[ModelProviderPreset]


def _probe_preset(preset: _ProviderPreset) -> ModelProviderPreset:
    """Resolve one preset's class path with the resolver the save path uses.

    A provider whose package is not installed (``langchain_ollama`` is an
    optional extra) would otherwise be selectable in the dropdown and then
    rejected by ``validate_candidate_text`` at save time with no explanation.
    Reporting ``available: false`` + the resolver's install hint turns that dead
    end into guidance.
    """
    try:
        resolve_class(preset.use)
    except Exception as exc:  # noqa: BLE001 — any import/attribute failure is the availability answer
        return ModelProviderPreset(**preset._asdict(), available=False, reason=str(exc) or exc.__class__.__name__)
    return ModelProviderPreset(**preset._asdict(), available=True, reason=None)


@router.get(
    "/models/providers",
    response_model=ModelProvidersResponse,
    summary="List Model Provider Presets",
    description=(
        "List the providers the model form offers by label (admin only), so an operator never types a class path. "
        "Each preset's `use` is resolved through `deerflow.reflection.resolve_class` — the same resolver the save path validates with: "
        "when the package is not installed the entry is returned with `available: false` and the resolver's install hint in `reason`, "
        "so the dropdown can disable it and explain itself instead of letting the operator pick something the save will reject."
    ),
)
async def list_model_providers(request: Request) -> ModelProvidersResponse:
    """Return the preset table with each class path probed for availability."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    return ModelProvidersResponse(providers=[_probe_preset(preset) for preset in _PROVIDER_PRESETS])


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


class ModelTestResponse(BaseModel):
    """Result of ``POST /api/models/test``.

    A probe is a *business* result, not an HTTP error: a refused connection or a
    rejected key is reported as ``ok: false`` with HTTP 200, so the UI renders a
    plain message instead of treating a normal "your key is wrong" as a server
    fault.
    """

    ok: bool = Field(..., description="True when the provider answered the probe request")
    latency_ms: int = Field(..., description="Wall-clock duration of the probe attempt in milliseconds")
    error: str | None = Field(None, description="Readable failure reason when ok is false; null on success")


#: Probe prompt — the cheapest possible round trip that still proves the base
#: URL, credential, and model name all work. Plain string, no tools, no system
#: prompt: the point is provider validation, not an answer.
_PROBE_PROMPT = "ping"

#: Bounded probe budget. A live provider answers a one-token prompt in well
#: under 3s, TLS handshake included, and the failures this endpoint exists to
#: surface (DNS, connection refused, 401/404) come back faster still. 15s leaves
#: headroom for a cold connection queued behind other traffic on a loaded
#: gateway while keeping an admin request from hanging on a black-holed
#: endpoint. Tests override this constant to exercise the timeout path quickly.
_PROBE_TIMEOUT_SECONDS = 15.0


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
    # `read_config_text`, NOT `.read_text()`: the latter applies universal-newline
    # translation, so a CRLF config is read as LF and the committed file ends up with
    # every line changed despite the edit being confined to the managed region. The
    # target machine's config.yaml is 100% CRLF, so this is the common path.
    text = read_config_text(config_path)
    candidate = replace_managed_section(text, models)
    validate_candidate_text(candidate, dir_path=config_path.parent)
    return commit_config_update(config_path, candidate)


def _reject(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=f"Invalid model configuration: {exc}")


def _probe_candidate_config(payload: dict) -> tuple[AppConfig, str]:
    """Build an ``AppConfig`` carrying *payload* as an extra, unsaved model.

    The probe must see the candidate exactly as the user typed it — including a
    cleartext ``api_key_value`` behind a ``$VAR`` that is not in ``.env`` yet —
    without a single byte touching disk. Two transformations do that in memory:

    1. ``$VAR`` references are resolved from the payload and ``os.environ``
       first, so ``api_key_value`` can stand in for a variable that does not
       exist yet. ``os.environ`` is read, never written — the endpoint is a
       probe, and persisting the secret is the write endpoints' job.
    2. The resolved entry is appended to the running config's own dump and
       re-validated. The dump/validate round trip — rather than
       ``model_copy(update=...)`` — is load-bearing: ``model_copy`` skips
       validators, so the ``_build_name_indexes`` hook would never run and
       ``create_chat_model``'s ``get_model_config(name)`` lookup would miss the
       candidate. Re-validating the whole config costs ~1ms, which is noise
       beside the network probe it guards.

    Returns the config and the candidate's name. Raises ``ValueError`` when the
    payload is not a valid entry — the caller reports that as ``ok: false``
    rather than a 500.
    """
    entry = build_model_entry(payload)
    entry.pop(_API_KEY_VALUE_FIELD, None)
    for meta_key in _ENTRY_META_KEYS:
        entry.pop(meta_key, None)

    api_key = entry.get("api_key")
    if isinstance(api_key, str) and api_key.startswith("$"):
        secret = payload.get(_API_KEY_VALUE_FIELD)
        entry["api_key"] = secret if isinstance(secret, str) and secret else os.getenv(api_key[1:])

    name = entry["name"]
    base = get_app_config()
    dumped = base.model_dump()
    return AppConfig.model_validate({**dumped, "models": [*dumped["models"], ModelConfig.model_validate(entry).model_dump()]}), name


async def _run_probe(payload: dict) -> ModelTestResponse:
    """Make one real, minimal request against *payload* and report the outcome.

    Every failure mode — an unresolvable ``use``, a provider exception, the
    timeout — collapses to ``ok: false`` with a readable message. ``CancelledError``
    still propagates, so a client disconnect aborts the request as usual.
    """
    started = time.perf_counter()
    try:
        app_config, name = _probe_candidate_config(payload)
        # Tracing is attached at the graph root for real runs; a standalone probe
        # would otherwise emit a second, orphaned trace per click.
        model = model_factory.create_chat_model(name, app_config=app_config, attach_tracing=False)
        await asyncio.wait_for(model.ainvoke(_PROBE_PROMPT), timeout=_PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        # `wait_for`'s own budget expiry and a `TimeoutError` raised inside the
        # provider both land here, so the message reports the measured elapsed
        # time rather than the budget — it is accurate in both cases.
        return ModelTestResponse(ok=False, latency_ms=_elapsed_ms(started), error=f"Timed out after {_elapsed_ms(started) / 1000:.1f}s waiting for the provider to answer.")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — any provider/reflection failure is a business result here
        return ModelTestResponse(ok=False, latency_ms=_elapsed_ms(started), error=_readable_error(exc))
    return ModelTestResponse(ok=True, latency_ms=_elapsed_ms(started), error=None)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _readable_error(exc: Exception) -> str:
    """Flatten an exception into one message an admin can act on.

    Provider SDKs wrap the useful part (``401 Incorrect API key provided``) in a
    chain of generic outer messages, so the chain is joined rather than showing
    only the outermost ``str(exc)``. Empty messages are skipped so a class with
    a blank ``__str__`` still yields something readable.
    """
    messages: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        text = str(current).strip()
        if text and text not in messages:
            messages.append(text)
        current = current.__cause__ if isinstance(current.__cause__, Exception) else current.__context__ if isinstance(current.__context__, Exception) else None
    return " | ".join(messages) or exc.__class__.__name__


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


@router.post(
    "/models/test",
    response_model=ModelTestResponse,
    summary="Test Model Connectivity",
    description=(
        "Make one real, minimal chat request against a candidate model entry and report whether it worked (admin only). "
        "The candidate is built in memory and never written to config.yaml or .env, so a model can be tested before it is saved; "
        "`api_key_value` supplies the cleartext behind a `$VAR` that does not exist yet. "
        "A failed connection, a rejected key, a bad model name, or an unresolvable `use` are all reported as `ok: false` with HTTP 200 — "
        "the probe answers 'does this work?', and 'no' is a normal answer. The attempt is bounded by a server-side timeout."
    ),
)
async def test_managed_model(request: Request, entry: ManagedModelEntry) -> ModelTestResponse:
    """Probe one candidate model entry without persisting anything."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    # Deliberately no `_resolve_config_path()` call: that helper's 500 is right
    # for the write endpoints, but this endpoint's contract is that a well-formed
    # admin request never returns 500. A missing config.yaml surfaces from
    # `get_app_config()` inside the probe as `ok: false` with the same message.
    return await _run_probe(entry.model_dump(exclude_none=True))


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
