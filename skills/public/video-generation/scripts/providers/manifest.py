"""Capability manifest shared by all video-generation providers.

Single source of truth for capability data (refactor-df-4):

- runtime validation reads it (`_validate` / `create_task`);
- `check_materials.py` derives its image-spec table from `image_spec`;
- `generate.py --describe-provider` renders it to stdout (no doc copies).

Field admission rule: a field must have a consumer (runtime check / describe
output / parity test). Declaration-only fields are not allowed.

Rendering rules for --describe (implemented in generate.py):

- degenerate values render honestly: `resolutions=()` / `duration_range=None`
  -> "model defaults"; `default_resolution=None` -> no default annotation;
  `image_spec=None` -> "no local preflight"; `ratios=()` -> "not honored
  (model-side)" (legacy minimax_v1);
- "(-1 auto)" is annotated only when the model's locks contain
  "frame_ratio_adaptive" (frame tasks default duration to -1). Passing -1 on
  models without the lock passes local validation today (pre-existing lenient
  behavior, kept as-is); the annotation documents support, it does not widen it;
- image roles come from each adapter module's IMAGE_ROLES constant (not part
  of ProviderManifest — seedance has no single-tail-frame mode);
- prompt_format_files paths are POSIX-style string literals by convention —
  never write OS separators into them;
- `params honored` renders in sorted order (frozenset iteration is unstable).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ImageSpec:
    side_min: int
    side_max: int
    ratio_min: float
    ratio_max: float
    extensions: frozenset[str]
    label: str


@dataclass(frozen=True)
class ModelSpec:
    name: str
    display_name: str
    description: str  # catalog copy (best-for guidance); registry.py keeps its own frontend copy, description-level parity enforced
    default_resolution: str | None = None  # None = model-side default (legacy minimax_v1)
    resolutions: tuple[str, ...] = ()  # () = model-side defaults
    duration_range: tuple[int, int] | None = None  # None = model-side defaults; -1 auto is interpreted by the adapter
    max_ref_images: int = 0
    max_ref_videos: int = 0
    max_ref_audios: int = 0
    locks: tuple[str, ...] = ()  # "frame_ratio_adaptive" / "edit_duration_-1" (upstream task semantics; edit tasks are not exposed by this skill)
    audio_only_ok: bool = False
    # Model-level capability gates for params the provider accepts as a whole
    # (e.g. minimax_h3 offers --upscale-video, but only H3 has it). Adapters
    # pre-check these before submission; describe annotates them per model.
    supports_reference: bool = True  # Ref2VA image-reference mode
    supports_regeneration: bool = True  # 2K upgrade via --upscale-video


@dataclass(frozen=True)
class ProviderManifest:
    name: str
    display_name: str
    description: str
    api_key_envs: tuple[str, ...]  # dedicated key first, fallback keys after
    default_model: str
    models: tuple[ModelSpec, ...]
    supported_params: frozenset[str]  # consumed by warn_ignored
    ratios: tuple[str, ...] = ()  # legal aspect-ratio enum; () = ratio not honored (legacy minimax_v1)
    prompt_format_files: dict[str, str] = field(default_factory=dict)  # mode -> references/providers/{p}/xxx.md
    image_spec: ImageSpec | None = None  # None = no local preflight (legacy minimax_v1)
