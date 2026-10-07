from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image

from providers import PROVIDERS

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency
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


def _load_image_editing_config() -> dict:
    if yaml is None:
        return {}

    config_path = _find_config_path()
    if not config_path:
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"Warning: failed to load image_editing config from {config_path}: {e}")
        return {}

    image_editing = config.get("image_editing", {})
    return image_editing if isinstance(image_editing, dict) else {}


def _resolve_env_variables(value):
    if isinstance(value, str):
        if value.startswith("$"):
            return os.getenv(value[1:], "")
        return value
    if isinstance(value, dict):
        return {key: _resolve_env_variables(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_resolve_env_variables(item) for item in value]
    return value


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
    env_model = os.getenv("IMAGE_EDITING_MODEL")
    if env_model:
        return env_model
    return _first_model(_provider_config(config, provider))


def validate_image(image_path: str) -> bool:
    try:
        with Image.open(image_path) as img:
            img.verify()
        with Image.open(image_path) as img:
            img.load()
        return True
    except Exception as e:
        print(f"Warning: Image '{image_path}' is invalid or corrupted: {e}")
        return False


def edit_image(
    image_paths: list[str],
    prompt_text: str,
    output_file: str,
    provider: str | None = None,
    model: str | None = None,
    size: str = "auto",
    quality: str = "high",
    output_format: str = "png",
    mask: str | None = None,
) -> str:
    if mask:
        raise ValueError("mask is not supported yet")
    if not image_paths:
        raise ValueError("At least one input image is required")

    output_path = Path(output_file)
    if output_path.exists():
        raise FileExistsError(
            f"Output file already exists and will not be overwritten: {output_file}. "
            "Choose a unique output filename."
        )

    image_editing_config = _load_image_editing_config()
    image_editing_config = _resolve_env_variables(image_editing_config)
    selected_provider = (
        provider
        or os.getenv("IMAGE_EDITING_PROVIDER")
        or image_editing_config.get("default_provider")
        or _first_configured_provider(image_editing_config)
        or "openai_image_edit"
    )
    configured_provider_names = _configured_provider_names(image_editing_config)
    if configured_provider_names and selected_provider not in configured_provider_names:
        supported = ", ".join(configured_provider_names)
        raise ValueError(
            f"Image editing provider '{selected_provider}' is not enabled in config.yaml. "
            f"Enabled providers: {supported}"
        )
    if selected_provider not in PROVIDERS:
        supported = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown image editing provider: {selected_provider}. Supported: {supported}")

    valid_image_paths = []
    for image_path in image_paths:
        if validate_image(image_path):
            valid_image_paths.append(image_path)
        else:
            print(f"Skipping invalid input image: {image_path}")

    if not valid_image_paths:
        raise ValueError("No valid input images were provided")

    if len(valid_image_paths) < len(image_paths):
        print(
            "Note: "
            f"{len(image_paths) - len(valid_image_paths)} input image(s) "
            "were skipped due to validation failure."
        )

    if not prompt_text.strip():
        raise ValueError("Prompt cannot be empty")

    provider_config = _provider_config(image_editing_config, selected_provider)
    authorization = (
        provider_config.get("Authorization")
        or provider_config.get("authorization")
        or os.getenv("IMAGE_EDITING_AUTHORIZATION")
        or os.getenv("OPENAI_IMAGE_AUTHORIZATION")
    )
    # Only providers that authenticate with an operator-supplied credential need
    # this; one that derives its own header opts out by setting
    # REQUIRES_AUTHORIZATION = False on its callable.
    if not authorization and getattr(PROVIDERS[selected_provider], "REQUIRES_AUTHORIZATION", True):
        raise ValueError(
            f"Image editing provider '{selected_provider}' is missing Authorization in config.yaml"
        )
    base_url = provider_config.get("base_url") or os.getenv("IMAGE_EDITING_BASE_URL") or os.getenv("OPENAI_IMAGE_BASE_URL")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError(
            f"Image editing provider '{selected_provider}' is missing base_url in config.yaml"
        )
    api_version = provider_config.get("api_version") or os.getenv("IMAGE_EDITING_API_VERSION") or os.getenv("OPENAI_IMAGE_API_VERSION")
    timeout_seconds = provider_config.get("timeout") or os.getenv("IMAGE_EDITING_TIMEOUT_SECONDS") or os.getenv("OPENAI_IMAGE_TIMEOUT_SECONDS")
    timeout_seconds = int(timeout_seconds) if timeout_seconds is not None else 300
    if provider_config.get("size"):
        size = provider_config["size"]
    if provider_config.get("quality"):
        quality = provider_config["quality"]
    if provider_config.get("output_format"):
        output_format = provider_config["output_format"]

    return PROVIDERS[selected_provider](
        prompt_text=prompt_text,
        reference_images=valid_image_paths,
        output_file=output_file,
        authorization=str(authorization) if authorization else "",
        base_url=str(base_url),
        timeout_seconds=timeout_seconds,
        size=size,
        model=_resolve_model(image_editing_config, selected_provider, model),
        quality=quality,
        output_format=output_format,
        api_version=str(api_version) if api_version else None,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Edit images using a configured provider")
    parser.add_argument("--image", action="append", required=True, help="Absolute path to input image")
    parser.add_argument("--prompt", required=True, help="Direct edit prompt text")
    parser.add_argument("--output-file", required=True, help="Output path for edited image")
    parser.add_argument("--provider", required=False, default=None, help="Image editing provider")
    parser.add_argument("--model", required=False, default=None, help="Provider model name")
    parser.add_argument("--size", required=False, default="auto", help="Edit size, default auto")
    parser.add_argument("--quality", required=False, default="high", help="Edit quality, default high")
    parser.add_argument("--output-format", required=False, default="png", help="Output format")
    parser.add_argument("--mask", required=False, default=None, help="Mask image path (not supported yet)")

    args = parser.parse_args()

    try:
        print(
            edit_image(
                args.image,
                args.prompt,
                args.output_file,
                args.provider,
                args.model,
                args.size,
                args.quality,
                args.output_format,
                args.mask,
            )
        )
    except Exception as e:
        print(f"Error while editing image: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
