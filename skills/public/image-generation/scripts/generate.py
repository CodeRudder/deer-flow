import json
import os
import sys
from pathlib import Path

from PIL import Image

from providers import PROVIDERS

try:
    import yaml
except ImportError:
    yaml = None


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


def _load_image_generation_config() -> dict:
    if yaml is None:
        return {}

    config_path = _find_config_path()
    if not config_path:
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"Warning: failed to load image_generation config from {config_path}: {e}")
        return {}

    image_generation = config.get("image_generation", {})
    return image_generation if isinstance(image_generation, dict) else {}


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


def _model_names(provider_config: dict) -> list[str]:
    models = provider_config.get("models")
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for model_config in models:
        if isinstance(model_config, str) and model_config:
            names.append(model_config)
            continue
        if not isinstance(model_config, dict):
            continue
        model = model_config.get("name") or model_config.get("model")
        if isinstance(model, str) and model:
            names.append(model)
    return names


def _first_model(provider_config: dict) -> str | None:
    return next(iter(_model_names(provider_config)), None)


def _provider_for_model(config: dict, model: str) -> str | None:
    """Which provider declares this model. Model names are unique across
    providers, so the first match wins."""
    for provider_config in _provider_configs(config):
        if model in _model_names(provider_config):
            name = provider_config.get("name")
            if isinstance(name, str) and name:
                return name
    return None


def _all_model_names(config: dict) -> list[str]:
    return [name for provider_config in _provider_configs(config) for name in _model_names(provider_config)]


def _resolve_target(config: dict, provider: str | None, model: str | None) -> tuple[str, str | None]:
    """Resolve (provider, model). The model name is the routing key: the provider
    is reverse-looked-up from config.yaml, so callers only pass --model.
    --provider stays as an escape hatch for debugging and for models that are not
    declared in config.yaml."""
    requested_model = model or os.getenv("IMAGE_GENERATION_MODEL")
    # An explicit --model or IMAGE_GENERATION_MODEL resolves its owning provider;
    # IMAGE_GENERATION_PROVIDER only fills in when no model is pinned, so the two
    # env vars can never cross-pair a provider with a foreign model.
    selected = provider or (None if requested_model else os.getenv("IMAGE_GENERATION_PROVIDER"))

    if not selected and requested_model:
        selected = _provider_for_model(config, requested_model)
        # Declared providers but no owner for this model = misconfiguration, not a
        # reason to guess a provider.
        if not selected and _provider_configs(config):
            declared = ", ".join(_all_model_names(config)) or "(none)"
            raise ValueError(
                f"Image generation model '{requested_model}' is not declared in config.yaml "
                f"image_generation.providers[].models[]. Declared models: {declared}"
            )

    # Keep this fallback in sync with harness/deerflow/image_generation/registry.py.
    selected = selected or _first_configured_provider(config) or "qwen_image"

    configured_provider_names = _configured_provider_names(config)
    if configured_provider_names and selected not in configured_provider_names:
        supported = ", ".join(configured_provider_names)
        raise ValueError(f"Image generation provider '{selected}' is not enabled in config.yaml. Enabled providers: {supported}")
    if selected not in PROVIDERS:
        supported = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown image generation provider: {selected}. Supported: {supported}")

    return selected, requested_model or _first_model(_provider_config(config, selected))


def validate_image(image_path: str) -> bool:
    """
    Validate if an image file can be opened and is not corrupted.

    Args:
        image_path: Path to the image file

    Returns:
        True if the image is valid and can be opened, False otherwise
    """
    try:
        with Image.open(image_path) as img:
            img.verify()
        with Image.open(image_path) as img:
            img.load()
        return True
    except Exception as e:
        print(f"Warning: Image '{image_path}' is invalid or corrupted: {e}")
        return False


def _read_prompt(prompt_file: str, provider: str) -> tuple[str, str | None]:
    prompt_text = Path(prompt_file).read_text(encoding="utf-8")
    negative_prompt = None

    try:
        prompt_json = json.loads(prompt_text)
    except json.JSONDecodeError:
        return prompt_text, None

    if isinstance(prompt_json, dict):
        prompt = prompt_json.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            prompt_text = prompt
        prompt_negative = prompt_json.get("negative_prompt")
        if isinstance(prompt_negative, str) and prompt_negative.strip():
            negative_prompt = prompt_negative

    return prompt_text, negative_prompt


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _coerce_optional_bool(value) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    return _parse_bool(str(value))


def generate_image(
    prompt_file: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str = "1:1",
    provider: str | None = None,
    model: str | None = None,
    negative_prompt: str | None = None,
    prompt_extend: bool | None = None,
    watermark: bool | None = None,
) -> str:
    output_path = Path(output_file)
    if output_path.exists():
        raise FileExistsError(
            f"Output file already exists and will not be overwritten: {output_file}. "
            "Choose a unique output filename."
        )

    image_generation_config = _load_image_generation_config()
    selected_provider, selected_model = _resolve_target(image_generation_config, provider, model)

    valid_reference_images = []
    for ref_img in reference_images:
        if validate_image(ref_img):
            valid_reference_images.append(ref_img)
        else:
            print(f"Skipping invalid reference image: {ref_img}")

    if len(valid_reference_images) < len(reference_images):
        print(
            "Note: "
            f"{len(reference_images) - len(valid_reference_images)} reference image(s) "
            "were skipped due to validation failure."
        )

    prompt_text, prompt_negative = _read_prompt(prompt_file, selected_provider)
    return PROVIDERS[selected_provider](
        prompt_text=prompt_text,
        reference_images=valid_reference_images,
        output_file=output_file,
        aspect_ratio=aspect_ratio,
        model=selected_model,
        negative_prompt=negative_prompt or prompt_negative,
        prompt_extend=_coerce_optional_bool(
            prompt_extend
            if prompt_extend is not None
            else os.getenv("IMAGE_GENERATION_PROMPT_EXTEND")
        ),
        watermark=_coerce_optional_bool(
            watermark
            if watermark is not None
            else os.getenv("IMAGE_GENERATION_WATERMARK")
        ),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate images using a configured provider")
    parser.add_argument(
        "--prompt-file",
        required=True,
        help="Absolute path to JSON prompt file",
    )
    parser.add_argument(
        "--reference-images",
        nargs="*",
        default=[],
        help="Absolute paths to reference images (space-separated)",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        help="Output path for generated image",
    )
    parser.add_argument(
        "--aspect-ratio",
        required=False,
        default="1:1",
        help="Aspect ratio of the generated image",
    )
    parser.add_argument(
        "--provider",
        required=False,
        default=None,
        help="Image generation provider, e.g. qwen_image or openai_image",
    )
    parser.add_argument(
        "--model",
        required=False,
        default=None,
        help="Provider model name",
    )
    parser.add_argument(
        "--negative-prompt",
        required=False,
        default=None,
        help="Negative prompt for providers that support it",
    )
    parser.add_argument(
        "--prompt-extend",
        required=False,
        default=None,
        help="Whether to let the provider extend the prompt. true/false.",
    )
    parser.add_argument(
        "--watermark",
        required=False,
        default=None,
        help="Whether to add provider watermark. true/false.",
    )

    args = parser.parse_args()

    try:
        print(
            generate_image(
                args.prompt_file,
                args.reference_images,
                args.output_file,
                args.aspect_ratio,
                args.provider,
                args.model,
                args.negative_prompt,
                _parse_bool(args.prompt_extend),
                _parse_bool(args.watermark),
            )
        )
    except Exception as e:
        print(f"Error while generating image: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
