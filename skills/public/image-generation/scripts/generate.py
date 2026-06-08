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
    env_model = os.getenv("IMAGE_GENERATION_MODEL")
    if env_model:
        return env_model
    return _first_model(_provider_config(config, provider))


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
    selected_provider = (
        provider
        or os.getenv("IMAGE_GENERATION_PROVIDER")
        or _first_configured_provider(image_generation_config)
        # Keep this fallback in sync with harness/deerflow/image_generation/registry.py.
        or "qwen_image"
    )
    configured_provider_names = _configured_provider_names(image_generation_config)
    if configured_provider_names and selected_provider not in configured_provider_names:
        supported = ", ".join(configured_provider_names)
        raise ValueError(
            f"Image generation provider '{selected_provider}' is not enabled in config.yaml. "
            f"Enabled providers: {supported}"
        )
    if selected_provider not in PROVIDERS:
        supported = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown image generation provider: {selected_provider}. Supported: {supported}")

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
        model=_resolve_model(image_generation_config, selected_provider, model),
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
        help="Image generation provider, e.g. gemini or qwen_image",
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
