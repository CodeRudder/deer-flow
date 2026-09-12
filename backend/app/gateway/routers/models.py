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
from deerflow.config.models_section import build_model_entry, commit_config_update, load_managed_models, read_config_text, replace_managed_section, to_public, upsert_env_var, validate_candidate_text, validate_model_entry
from deerflow.models import factory as model_factory
from deerflow.models.image_generation import ImageGenerationProvidersResponse, get_image_generation_providers
from deerflow.models.video_generation import VideoGenerationModel, VideoGenerationProvider, VideoGenerationProvidersResponse, get_video_generation_providers
from deerflow.persistence.engine import get_session_factory
from deerflow.reflection import resolve_class

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["models"])

_ADMIN_REQUIRED_DETAIL = "Admin privileges required to manage model configuration."

#: Default thinking budget prefill. The operator asked for "thinking output should
#: not exceed 4k by default"; Anthropic also wants budget < max_tokens, and
#: ChatAnthropic's own max_tokens default is exactly 4096 (measured) — equal, not
#: less — so the form pairs this with an 8192 max_tokens default.
DEFAULT_BUDGET_TOKENS = 4096


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

    ``api_base_field`` is the **constructor keyword the provider actually
    accepts**, and it is NOT the same across providers — measured against each
    class's ``model_fields``:

        openai      -> openai_api_base
        anthropic   -> anthropic_api_url
        google      -> base_url
        deepseek et al. (patched) -> api_base

    Writing the wrong key is silent: ``ModelConfig`` is ``extra="allow"``, so
    the value is accepted at save time and forwarded into the SDK's
    ``model_kwargs``, failing only when a request is actually made — e.g.
    ``AsyncMessages.create() got an unexpected keyword argument 'api_base'``.
    Hence the per-provider field name rather than one shared ``api_base``.

    ``None`` means the provider exposes no direct base-URL constructor field;
    ``api_base_in_model_kwargs`` then carries the value through ``model_kwargs``
    instead.
    """

    key: str
    label: str
    use: str
    default_api_base: str | None = None
    api_base_field: str | None = "api_base"
    api_base_in_model_kwargs: bool = False
    thinking: "_ThinkingPreset | None" = None


class _ThinkingPreset(NamedTuple):
    """How one provider spells "turn thinking on".

    The shape is NOT shared — same lesson as ``api_base_field``. Measured against
    the commented examples in ``config.example.yaml``:

        anthropic / openai-compatible -> ``thinking`` / ``extra_body.thinking``
        google                        -> ``thinking_budget``
        vllm (Qwen)                   -> ``extra_body.chat_template_kwargs``

    ``needs_budget`` marks providers whose API requires an explicit thinking
    budget (Anthropic: min 1024 and must be < max_tokens). The form only shows
    the budget input for those.

    ``enabled`` / ``disabled`` are written verbatim as ``when_thinking_enabled``
    / ``when_thinking_disabled``.
    """

    enabled: dict
    disabled: dict
    needs_budget: bool = False


#: Bytes-level templates, one per provider family. Referenced by the rows below
#: so the table stays readable.
_THINKING_EXTRA_BODY = _ThinkingPreset(
    enabled={"extra_body": {"thinking": {"type": "enabled"}}},
    disabled={"extra_body": {"thinking": {"type": "disabled"}}},
)
_THINKING_ANTHROPIC = _ThinkingPreset(
    # budget_tokens is required by the Anthropic API whenever type=enabled and
    # has no server default; the form overrides the 4096 below when the operator
    # edits the field.
    enabled={"thinking": {"type": "enabled", "budget_tokens": 4096}},
    disabled={"thinking": {"type": "disabled"}},
    needs_budget=True,
)
_THINKING_GOOGLE = _ThinkingPreset(
    enabled={"thinking_budget": 4096},
    disabled={"thinking_budget": 0},
    needs_budget=True,
)
_THINKING_VLLM = _ThinkingPreset(
    enabled={"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}},
    disabled={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
)


#: The provider dropdown, in display order. Extending the form means adding one
#: row here — the availability probe, the endpoint key and the thinking template
#: are all generic over the table.
#: `openai-compatible` is the deliberate fallback for a hand-crafted entry whose
#: `use` matches no row: the UI shows that label but keeps the stored class path
#: verbatim, so an advanced configuration survives an edit untouched.
_PROVIDER_PRESETS: tuple[_ProviderPreset, ...] = (
    _ProviderPreset("openai", "OpenAI", "langchain_openai:ChatOpenAI", None, "openai_api_base", thinking=_THINKING_EXTRA_BODY),
    _ProviderPreset("openai-compatible", "其他 OpenAI 兼容 (OpenAI-compatible)", "langchain_openai:ChatOpenAI", None, "openai_api_base", thinking=_THINKING_EXTRA_BODY),
    _ProviderPreset("doubao", "豆包 (火山方舟)", "deerflow.models.patched_deepseek:PatchedChatDeepSeek", "https://ark.cn-beijing.volces.com/api/v3", "api_base", thinking=_THINKING_EXTRA_BODY),
    _ProviderPreset("deepseek", "DeepSeek", "deerflow.models.patched_deepseek:PatchedChatDeepSeek", "https://api.deepseek.com/v1", "api_base", thinking=_THINKING_EXTRA_BODY),
    _ProviderPreset("kimi", "Kimi (Moonshot)", "deerflow.models.patched_deepseek:PatchedChatDeepSeek", "https://api.moonshot.cn/v1", "api_base", thinking=_THINKING_EXTRA_BODY),
    # MiniMax subclasses ChatOpenAI, so it takes the OpenAI-compatible field.
    _ProviderPreset("minimax", "MiniMax", "deerflow.models.patched_minimax:PatchedChatMiniMax", None, "openai_api_base", thinking=_THINKING_EXTRA_BODY),
    _ProviderPreset("anthropic", "Anthropic Claude", "langchain_anthropic:ChatAnthropic", None, "anthropic_api_url", thinking=_THINKING_ANTHROPIC),
    _ProviderPreset("google", "Google Gemini", "langchain_google_genai:ChatGoogleGenerativeAI", None, "base_url", thinking=_THINKING_GOOGLE),
    _ProviderPreset("vllm", "vLLM (Qwen 等)", "deerflow.models.vllm_provider:VllmChatModel", None, "openai_api_base", thinking=_THINKING_VLLM),
    _ProviderPreset("ollama", "Ollama (本地)", "langchain_ollama:ChatOllama", None, "base_url", thinking=_THINKING_VLLM),
)


class ModelProviderPreset(BaseModel):
    """A provider row plus the result of probing its class path."""

    key: str = Field(..., description="Stable identifier the form stores in its provider field")
    label: str = Field(..., description="Human-readable provider name")
    use: str = Field(..., description="Class path an entry using this provider carries")
    default_api_base: str | None = Field(None, description="Endpoint to prefill; null means the SDK default")
    api_base_field: str | None = Field(
        None,
        description=(
            "Constructor keyword this provider accepts for its endpoint (e.g. `openai_api_base`, `anthropic_api_url`, "
            "`base_url`). The form writes the base URL under THIS key — writing a shared `api_base` silently lands in "
            "the SDK's model_kwargs and only fails at request time. Null when the provider has no such field."
        ),
    )
    available: bool = Field(..., description="True when the class path resolves in this process")
    reason: str | None = Field(None, description="Why the class path did not resolve; null when it did")
    supports_thinking: bool = Field(False, description="Whether this provider has a thinking template at all")
    thinking_enabled: dict | None = Field(
        None,
        description=(
            "The `when_thinking_enabled` block to write verbatim when the operator ticks 'supports thinking'. "
            "The shape is per-provider (Anthropic `thinking`, OpenAI-compatible `extra_body.thinking`, "
            "Google `thinking_budget`, vLLM `chat_template_kwargs`) — there is no shared form."
        ),
    )
    thinking_disabled: dict | None = Field(None, description="The matching `when_thinking_disabled` block.")
    thinking_needs_budget: bool = Field(False, description="True when the provider's API requires an explicit thinking budget.")
    default_budget_tokens: int | None = Field(None, description="Prefill for the budget input; null when the provider does not need one.")


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
    thinking = preset.thinking
    # Flattened explicitly rather than via `preset._asdict()`: the preset carries
    # a nested `_ThinkingPreset`, which is not a field of the response model.
    response = {
        "key": preset.key,
        "label": preset.label,
        "use": preset.use,
        "default_api_base": preset.default_api_base,
        "api_base_field": preset.api_base_field,
        "supports_thinking": thinking is not None,
        "thinking_enabled": thinking.enabled if thinking else None,
        "thinking_disabled": thinking.disabled if thinking else None,
        "thinking_needs_budget": bool(thinking and thinking.needs_budget),
        "default_budget_tokens": (thinking.enabled.get("thinking", {}).get("budget_tokens") or DEFAULT_BUDGET_TOKENS) if thinking else None,
    }
    try:
        resolve_class(preset.use)
    except Exception as exc:  # noqa: BLE001 — any import/attribute failure is the availability answer
        return ModelProviderPreset(**response, available=False, reason=str(exc) or exc.__class__.__name__)
    return ModelProviderPreset(**response, available=True, reason=None)


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

#: The thinking probe needs a prompt that actually elicits reasoning — "ping"
#: may be answered without any deliberation, which would read as "no thinking"
#: even on an endpoint that supports it. A short arithmetic-with-steps question
#: reliably provokes a reasoning block on models that reason at all.
_THINKING_PROBE_PROMPT = "What is 17 * 23? Work through it step by step."

#: Bounded budget for `/models/test`. A live provider answers a one-token prompt
#: in well under 3s, TLS handshake included, and the failures this endpoint
#: exists to surface (DNS, connection refused, 401/404) come back faster still.
#: 15s leaves headroom for a cold connection queued behind other traffic on a
#: loaded gateway while keeping an admin request from hanging on a black-holed
#: endpoint. Tests override this constant to exercise the timeout path quickly.
_PROBE_TIMEOUT_SECONDS = 15.0

#: Budget for the whole thinking probe — a different job from `/models/test`, so
#: a different number. That one sends a single one-token prompt; this one sends
#: two or three prompts that are *meant* to provoke reasoning, and reasoning is
#: most of the latency.
#:
#: Measured against a live Anthropic-compatible reasoning endpoint
#: (glm-5.3-flash via bigmodel.cn), the probe's calls end to end: 9.0s (no
#: thinking params) + 8.1s (thinking enabled, budget 4096) + 1.9s (thinking
#: disabled) ≈ 19-26s. The shared 15s budget could not fit even that first pass,
#: so the probe reported "timed out" on an endpoint answering every call in
#: under 10s — a false negative on exactly the case the feature exists to
#: measure.
#:
#: 60s is ~2.5x the measured worst case, which covers a slower or loaded
#: endpoint while still bounding an admin's wait on a black-holed one. It is a
#: budget for the whole probe, not per call: per-call budgets would let a stuck
#: endpoint cost 3x whatever number is chosen here. Every proxy in front of this
#: route allows far more (nginx is configured at 600s), so this is the binding
#: limit.
_THINKING_PROBE_TIMEOUT_SECONDS = 60.0


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


def _validate_candidate_matches_save(entry: dict) -> None:
    """Reject an entry a SAVE would reject, using the save path's own checks.

    Two of the save path's rules decide whether the provider call below can even
    work, so the probe must apply them or it will report a raw SDK error where
    Save would have given a readable 400:

    - ``use`` must resolve to a real class;
    - the endpoint key must be one the provider class accepts — the check that
      turns ``AsyncMessages.create() got an unexpected keyword argument
      'openai_api_base'`` into "use `anthropic_api_url` instead".

    Scope matters: only THIS entry is checked. Reusing ``validate_candidate_text``
    over the whole document would re-validate every unrelated stored entry, and a
    probe must not fail because some other model in the file is misconfigured —
    it is a question about one candidate, not a lint of the deployment.

    Raises ``ValueError`` with the save path's own message, so the two agree.
    """
    validate_model_entry(ModelConfig.model_validate(entry))


def _find_stored_entry(name: str) -> dict | None:
    """The entry *name* currently has in the managed region, or ``None``.

    Read straight from ``config.yaml`` rather than the cached ``AppConfig``:
    an entry written moments ago must be visible to a probe that follows, and
    only the file is guaranteed fresh. Returns ``None`` on any read/parse
    failure — a probe reports that as "no credential", never as a 500.
    """
    try:
        for entry in load_managed_models(_resolve_config_path()):
            if entry.get("name") == name:
                return entry
    except Exception:  # noqa: BLE001 — an unreadable config just means "nothing stored"
        return None
    return None


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
    name = entry["name"]

    # The probe must carry a usable credential, the same way a SAVE would.
    #
    # The edit form never shows a stored key — only its `$VAR` reference or a
    # mask — so a Detect click on an existing model posts no usable credential.
    # Save already treats a mask/absent key as "keep the stored one" (see
    # `_to_stored_entry`); the probe is a read-only preview of that same save and
    # has to agree. Measured in the live UI: editing glm-5.3-flash and clicking
    # Detect failed with "Could not resolve authentication method" against a
    # model that works, because the probe arrived keyless.
    #
    # `$VAR` is resolved HERE, not by the AppConfig round trip below:
    # `AppConfig.model_validate` does not run `resolve_env_variables` (only
    # `from_file` does), so a reference reaching the provider unresolved is what
    # the SDK reports as "could not resolve authentication method".
    #
    # A mask is not a credential at all, so it is dropped whenever there is no
    # stored value to fall back on.
    raw_key = entry.get("api_key")
    if not raw_key or (isinstance(raw_key, str) and _MASK in raw_key):
        # The UI sends the stored `$VAR` reference under this private key on an
        # edit (it is not a secret — the read API already returns it). It is
        # payload-only metadata and must never reach the model constructor,
        # where an unknown kwarg would be forwarded to the SDK.
        stored = payload.get("_stored_api_key")
        if not isinstance(stored, str) or not stored:
            existing = _find_stored_entry(name)
            stored = existing.get("api_key") if existing else None
        entry["api_key"] = stored if isinstance(stored, str) and stored else None
    entry.pop("_stored_api_key", None)

    api_key = entry.get("api_key")
    if isinstance(api_key, str) and api_key.startswith("$"):
        secret = payload.get(_API_KEY_VALUE_FIELD)
        entry["api_key"] = secret if isinstance(secret, str) and secret else os.getenv(api_key[1:])
    if not entry.get("api_key"):
        entry.pop("api_key", None)

    # Run the same validation a SAVE would, before anything is built.
    #
    # The probe used to skip straight to `AppConfig.model_validate`, which is not
    # the save path: save goes through `validate_candidate_text`, and that is
    # where the base-URL key check lives. Skipping it let the probe build a model
    # with an endpoint key its provider rejects and report the raw SDK error —
    # measured in the live UI: "AsyncMessages.create() got an unexpected keyword
    # argument 'openai_api_base'". Save would have rejected that entry with a
    # readable 400 naming the correct key, so a Detect click disagreed with the
    # Save button about the very same entry. Both must give the same answer.
    _validate_candidate_matches_save(entry)

    base = get_app_config()
    dumped = base.model_dump()
    candidate = ModelConfig.model_validate(entry).model_dump()
    # REPLACE an entry of the same name rather than appending beside it.
    #
    # Appending was a silent measurement bug. `create_chat_model(name, ...)`
    # resolves through `get_model_config`, which returns the FIRST entry with
    # that name — so as soon as the payload's name matched a stored model (the
    # normal case: the operator is editing an existing model and clicks Detect),
    # the lookup found the STORED entry and the probe reported the old config's
    # behaviour as the verdict on the new one. Measured on a live endpoint: a
    # candidate that provably sends `thinking: {type: disabled}` and gets no
    # thinking block back still reported `respects_disabled: false`, because the
    # stored entry (no thinking config) was the one being called.
    models = [m for m in dumped["models"] if m.get("name") != name]
    return AppConfig.model_validate({**dumped, "models": [*models, candidate]}), name


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


def _has_thinking_content(message: object) -> bool:
    """True when a response actually contains reasoning, not just a finished answer.

    The decisive observation from a live Anthropic-compatible endpoint: with
    thinking on, ``content`` is a list of typed blocks including ``thinking``;
    with it off, ``content`` is a plain string. So the presence of a thinking
    block IS the signal — "the request did not error" is not, because a
    compatible gateway can accept the parameters and answer without reasoning.

    Checked across every carrier the supported providers use, since they differ:
    an Anthropic-style ``content`` block list, and the ``reasoning_content`` /
    ``thinking`` keys OpenAI-compatible and patched providers put on
    ``additional_kwargs``.
    """
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                return True
            # Some providers emit the dict without a `type` discriminator.
            if isinstance(block, dict) and "thinking" in block:
                return True

    extra = getattr(message, "additional_kwargs", None) or {}
    if isinstance(extra, dict):
        for key in ("reasoning_content", "thinking"):
            value = extra.get(key)
            if isinstance(value, str) and value.strip():
                return True

    metadata = getattr(message, "response_metadata", None) or {}
    if isinstance(metadata, dict):
        for key in ("reasoning_content", "thinking"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return True
    return False


class ThinkingProbeResponse(BaseModel):
    """Result of ``POST /api/models/probe-thinking``.

    Three INDEPENDENT observations rather than one boolean, because "does this
    endpoint support thinking" is not a yes/no question in practice. Measured on
    a live Anthropic-compatible gateway: it returned thinking blocks even when
    the request carried NO thinking parameters at all, and still returned them
    when the parameters said ``disabled`` — so a single "supported: true" would
    have told the operator their thinking toggle works when it does not.

    Produced from two or three real calls — never more, because each one is a
    real reasoning request the operator waits on. ``respects_disabled`` is
    therefore a single sample: on endpoints that leak a thinking block
    intermittently it can read ``false`` for a toggle that does work, and the UI
    words it as an observation for exactly that reason.

    ``error`` is set only when the probe could not run at all (bad entry,
    unreachable provider); the three booleans are then all ``false`` and mean
    "unknown", not "no".
    """

    ok: bool = Field(..., description="False when the probe could not run; the findings are then all false and mean unknown")
    thinks_by_default: bool = Field(False, description="The endpoint produced reasoning with no thinking parameters at all")
    respects_enabled: bool = Field(False, description="Reasoning appeared when thinking was explicitly enabled")
    respects_disabled: bool = Field(False, description="Reasoning was absent when thinking was explicitly disabled")
    latency_ms: int = Field(0, description="Total wall-clock duration of the probe calls")
    error: str | None = Field(None, description="Why the probe could not run; null on success")


async def _ask_once(model: object) -> bool:
    """Send the thinking probe prompt once and report whether reasoning came back.

    Deliberately carries no timeout of its own — the budget belongs to the whole
    probe (see ``_run_thinking_probe``), not to each of its two-to-three calls.
    A per-call budget would let an admin wait 3 × 15s on a slow endpoint.
    """
    response = await model.ainvoke(_THINKING_PROBE_PROMPT)
    return _has_thinking_content(response)


async def _run_thinking_probe(payload: dict) -> ThinkingProbeResponse:
    """Run two or three real calls and report what the endpoint actually does.

    The third ("disabled") call is only issued when the first two showed the
    endpoint thinks at all — no point spending it on an endpoint that never
    reasons. Each call is a real reasoning request (6-9s measured), so the probe
    never sends more than three: it is held to two-to-three calls on purpose,
    and anything that would need a wider sample belongs in a repeated click, not
    in this budget.
    """
    started = time.perf_counter()
    try:
        app_config, name = _probe_candidate_config(payload)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — a bad entry is a business result, not a 500
        return ThinkingProbeResponse(ok=False, latency_ms=_elapsed_ms(started), error=_readable_error(exc))

    def build(entry: dict) -> object:
        cfg, _ = _probe_candidate_config(entry)
        return model_factory.create_chat_model(name, thinking_enabled=bool(entry.get("supports_thinking")), app_config=cfg, attach_tracing=False)

    async def observe() -> tuple[bool, bool, bool]:
        thinking_keys = ("supports_thinking", "when_thinking_enabled", "when_thinking_disabled", "thinking")

        # 1. The endpoint's OWN default. Every thinking key is stripped, so this
        #    is what the model does when the operator configures nothing.
        #    Without the strip the payload's own `when_thinking_disabled` rides
        #    along and this call silently measures "thinking disabled" instead —
        #    which reported `thinks_by_default: false` for an endpoint that
        #    plainly reasons unprompted.
        bare = {k: v for k, v in payload.items() if k not in thinking_keys}
        thinks_by_default = await _ask_once(build(bare))

        # 2. Explicitly enabled.
        respects_enabled = await _ask_once(
            build(
                {
                    **payload,
                    "supports_thinking": True,
                    "when_thinking_disabled": None,
                    "when_thinking_enabled": payload.get("when_thinking_enabled"),
                }
            )
        )

        # 3. Explicitly disabled — only worth asking if it reasons at all.
        if not (thinks_by_default or respects_enabled):
            return thinks_by_default, respects_enabled, True

        # `supports_thinking: False` is what makes this call actually test
        # DISABLING: it is passed through as `create_chat_model(thinking_enabled=...)`,
        # and the factory only consults `when_thinking_disabled` when that is
        # false. Left `True`, the enabled template wins and the call re-measures
        # "enabled" a second time.
        disabled_entry = {
            **payload,
            "supports_thinking": False,
            "when_thinking_enabled": None,
            "when_thinking_disabled": payload.get("when_thinking_disabled"),
        }
        # One sample, three calls total. Deliberately NOT repeated: each call is
        # a real reasoning request at 6-9s, and the operator is waiting on it.
        #
        # The cost of a single sample is that this endpoint leaks a thinking
        # block on a disabled request part of the time (measured: glm-5.3-flash
        # 2/8, glm-5.3 1/8), so `respects_disabled: false` can be a false alarm.
        # That is why the UI words it as an observation ("still returned thinking
        # blocks with thinking disabled") rather than a verdict, and why the
        # check is cheap to repeat by clicking Detect again.
        still_thinks = await _ask_once(build(disabled_entry))
        return thinks_by_default, respects_enabled, not still_thinks

    try:
        # One budget for the whole probe, so the worst case an admin waits is
        # bounded by a single number rather than three times a per-call one.
        thinks_by_default, respects_enabled, respects_disabled = await asyncio.wait_for(observe(), timeout=_THINKING_PROBE_TIMEOUT_SECONDS)
        del app_config
    except TimeoutError:
        return ThinkingProbeResponse(ok=False, latency_ms=_elapsed_ms(started), error=f"Timed out after {_elapsed_ms(started) / 1000:.1f}s waiting for the provider to answer.")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — any provider failure is a business result here
        return ThinkingProbeResponse(ok=False, latency_ms=_elapsed_ms(started), error=_readable_error(exc))

    return ThinkingProbeResponse(
        ok=True,
        thinks_by_default=thinks_by_default,
        respects_enabled=respects_enabled,
        respects_disabled=respects_disabled,
        latency_ms=_elapsed_ms(started),
        error=None,
    )


@router.post(
    "/models/probe-thinking",
    response_model=ThinkingProbeResponse,
    summary="Probe Thinking Behaviour",
    description=(
        "Make one to three real chat requests against a candidate entry and report what the endpoint actually does with thinking (admin only). "
        "Unlike `/models/test`, the question is not 'does it connect' but 'does reasoning happen, and can it be turned off'. "
        "The answer is three independent observations, because a single boolean misleads: a compatible gateway was measured returning reasoning even when "
        "the request carried no thinking parameters, and again when they said `disabled` — so `respects_disabled: false` is the signal that the UI's thinking "
        "toggle has no effect on that endpoint. Nothing is written to config.yaml or .env."
    ),
)
async def probe_model_thinking(request: Request, entry: ManagedModelEntry) -> ThinkingProbeResponse:
    """Observe thinking behaviour on one candidate entry without persisting anything."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    return await _run_thinking_probe(entry.model_dump(exclude_none=True))


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
