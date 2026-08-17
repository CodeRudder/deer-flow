import json
import os
import sys
from pathlib import Path

from providers import PROVIDERS

try:
    import yaml
except ImportError:
    yaml = None


# Old provider strings -> registry keys. The legacy "minimax" branch was Hailuo
# V1, so it must keep routing to minimax_v1 (not the new H3) to avoid silently
# changing behavior/billing for callers relying on the old env var.
_PROVIDER_ALIASES = {"minimax": "minimax_v1", "google": "gemini"}


def _find_config_path() -> Path | None:
    explicit_path = os.getenv("DEER_FLOW_CONFIG_PATH")
    if explicit_path:
        path = Path(explicit_path)
        return path if path.exists() else None

    candidates = [
        Path.cwd() / "config.yaml",
        Path.cwd().parent / "config.yaml",
        Path(__file__).resolve().parents[4] / "config.yaml",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _load_video_generation_config() -> dict:
    if yaml is None:
        return {}

    config_path = _find_config_path()
    if not config_path:
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        print(
            f"Warning: failed to load video_generation config from {config_path}: {e}"
        )
        return {}

    video_generation = config.get("video_generation", {})
    return video_generation if isinstance(video_generation, dict) else {}


def _provider_configs(config: dict) -> list[dict]:
    providers = config.get("providers")
    if not isinstance(providers, list):
        return []
    return [provider for provider in providers if isinstance(provider, dict)]


def _configured_provider_names(config: dict) -> list[str]:
    names: list[str] = []
    for provider_config in _provider_configs(config):
        provider = provider_config.get("name")
        if isinstance(provider, str) and provider:
            names.append(provider)
    return names


def _first_configured_provider(config: dict) -> str | None:
    return next(iter(_configured_provider_names(config)), None)


def _provider_config(config: dict, provider: str) -> dict:
    for provider_config in _provider_configs(config):
        if provider_config.get("name") == provider:
            return provider_config
    return {}


def _first_model(provider_config: dict) -> str | None:
    models = provider_config.get("models")
    if not isinstance(models, list):
        return None
    for model_config in models:
        if isinstance(model_config, str) and model_config:
            return model_config
        if not isinstance(model_config, dict):
            continue
        model = model_config.get("name") or model_config.get("model")
        if isinstance(model, str) and model:
            return model
    return None


def _resolve_model(config: dict, provider: str, model: str | None) -> str | None:
    if model:
        return model
    env_model = os.getenv("VIDEO_GENERATION_MODEL")
    if env_model:
        return env_model
    return _first_model(_provider_config(config, provider))


def _credential_fallback() -> str | None:
    """No config/override: pick by available credential. Gemini keeps the old
    default; a dedicated video key implies H3; the shared key keeps old behavior
    (legacy minimax_v1)."""
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("MINIMAX_VIDEO_API_KEY"):
        return "minimax_h3"
    if os.getenv("MINIMAX_API_KEY"):
        return "minimax_v1"
    return None


def _read_prompt(prompt_file: str) -> str:
    """Extract the natural-language prompt. If the file is JSON with a `prompt`
    field, use that (the rest is IR); otherwise treat the whole file as prompt.
    Never pass the raw JSON blob to the provider (AC-8)."""
    prompt_text = Path(prompt_file).read_text(encoding="utf-8")
    try:
        prompt_json = json.loads(prompt_text)
    except json.JSONDecodeError:
        return prompt_text
    if isinstance(prompt_json, dict):
        prompt = prompt_json.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return prompt
    return prompt_text


def _resolve_provider(config: dict, provider: str | None) -> str:
    selected = (
        provider
        or os.getenv("VIDEO_GENERATION_PROVIDER")
        or _first_configured_provider(config)
        or _credential_fallback()
    )
    if not selected:
        raise ValueError(
            "No video provider resolved. Set GEMINI_API_KEY or MINIMAX_VIDEO_API_KEY "
            "(or the shared MINIMAX_API_KEY), declare video_generation.providers[] in "
            "config.yaml, or pass --provider."
        )
    selected = selected.strip().lower()
    selected = _PROVIDER_ALIASES.get(selected, selected)

    configured = _configured_provider_names(config)
    if configured and selected not in configured:
        raise ValueError(
            f"Video generation provider '{selected}' is not enabled in config.yaml. "
            f"Enabled providers: {', '.join(configured)}"
        )
    if selected not in PROVIDERS:
        raise ValueError(
            f"Unknown video generation provider: {selected}. Supported: {', '.join(sorted(PROVIDERS))}"
        )
    return selected


def generate_video(
    prompt_file: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    resolution: str | None = None,
    duration: int | None = None,
    image_role: str | None = None,
) -> str:
    config = _load_video_generation_config()
    selected_provider = _resolve_provider(config, provider)

    prompt_text = _read_prompt(prompt_file)

    # Pass through only explicitly-set params so providers apply their own
    # defaults and unset values don't trigger spurious ignore warnings (AC-11).
    params = {
        "resolution": resolution,
        "duration": duration,
        "ratio": aspect_ratio,
        "image_role": image_role,
    }
    params = {k: v for k, v in params.items() if v is not None}

    adapter = PROVIDERS[selected_provider](
        model=_resolve_model(config, selected_provider, model)
    )
    return adapter.generate(prompt_text, reference_images, output_file, params)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate videos using a configured provider"
    )
    parser.add_argument(
        "--prompt-file", required=True, help="Absolute path to JSON prompt file"
    )
    parser.add_argument(
        "--reference-images",
        nargs="*",
        default=[],
        help="Absolute paths to reference images / first frame (space-separated)",
    )
    parser.add_argument(
        "--output-file", required=True, help="Output path for generated video"
    )
    parser.add_argument(
        "--aspect-ratio",
        default=None,
        help="Aspect ratio (T2V); ignored by some providers",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Video provider, e.g. minimax_h3, gemini, minimax_v1",
    )
    parser.add_argument("--model", default=None, help="Provider model name")
    parser.add_argument(
        "--resolution",
        default=None,
        help="Output resolution, e.g. 768P or 2K (provider-specific)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Video duration in seconds (provider-specific)",
    )
    parser.add_argument(
        "--image-role",
        default=None,
        choices=["first_frame", "last_frame", "first_last", "reference"],
        help=(
            "How to use --reference-images (MiniMax H3): first_frame (default, I2V), "
            "last_frame, first_last (first + optional last frame), or reference "
            "(up to 5 identity/style reference images). Frame roles and reference "
            "are mutually exclusive."
        ),
    )
    args = parser.parse_args()

    try:
        print(
            generate_video(
                args.prompt_file,
                args.reference_images,
                args.output_file,
                args.aspect_ratio,
                args.provider,
                args.model,
                args.resolution,
                args.duration,
                args.image_role,
            )
        )
    except Exception as e:
        print(f"Error while generating video: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
