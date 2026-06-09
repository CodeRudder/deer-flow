import base64
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import requests


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_MODE = "images_generations"
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_QUALITY = "high"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_CHAT_IMAGE_SIZE = "1K"
DEFAULT_TRUST_ENV = False


def _aspect_ratio_to_size(aspect_ratio: str) -> str:
    size_map = {
        "1:1": "2048x2048",
        "16:9": "3840x2160",
        "9:16": "2160x3840",
        "4:3": "3072x2304",
        "3:4": "2304x3072",
    }
    if aspect_ratio in size_map:
        return size_map[aspect_ratio]

    try:
        width_ratio, height_ratio = [float(part) for part in aspect_ratio.split(":", 1)]
    except ValueError as exc:
        raise ValueError(f"Invalid aspect ratio for openai_image: {aspect_ratio}") from exc

    if width_ratio <= 0 or height_ratio <= 0:
        raise ValueError(f"Invalid aspect ratio for openai_image: {aspect_ratio}")

    if width_ratio >= height_ratio:
        width = 2048
        height = int(width * height_ratio / width_ratio)
    else:
        height = 2048
        width = int(height * width_ratio / height_ratio)

    width = max(8, width - width % 8)
    height = max(8, height - height % 8)
    return f"{width}x{height}"


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for openai_image: {value}")


def _build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = _parse_bool(os.getenv("OPENAI_IMAGE_TRUST_ENV"), default=DEFAULT_TRUST_ENV)
    return session


def _build_payload(
    *,
    prompt_text: str,
    model: str,
    aspect_ratio: str,
) -> dict:
    payload = {
        "model": model,
        "prompt": prompt_text,
        "size": _aspect_ratio_to_size(aspect_ratio),
        "quality": os.getenv("OPENAI_IMAGE_QUALITY") or DEFAULT_QUALITY,
        "output_format": os.getenv("OPENAI_IMAGE_OUTPUT_FORMAT") or DEFAULT_OUTPUT_FORMAT,
        "n": 1,
    }
    response_format = os.getenv("OPENAI_IMAGE_RESPONSE_FORMAT")
    if response_format:
        payload["response_format"] = response_format
    return payload


def _build_chat_completions_payload(
    *,
    prompt_text: str,
    model: str,
    aspect_ratio: str,
) -> dict:
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt_text,
            }
        ],
        "modalities": ["image"],
        "image_config": {
            "aspect_ratio": aspect_ratio,
            "image_size": os.getenv("OPENAI_IMAGE_CHAT_IMAGE_SIZE") or DEFAULT_CHAT_IMAGE_SIZE,
        },
        "stream": False,
    }


def _download_image(session: requests.Session, image_url: str, output_file: str) -> None:
    response = session.get(image_url, timeout=(10, 60))
    if not response.ok:
        parsed = urlparse(image_url)
        raise RuntimeError(
            "provider=openai_image "
            f"message=failed to download image host={parsed.netloc} status={response.status_code}"
        )
    if not response.content:
        raise RuntimeError("provider=openai_image message=downloaded image is empty")

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)


def _write_b64_image(b64_json: str, output_file: str) -> None:
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(base64.b64decode(b64_json))


def _response_summary(response: requests.Response) -> str:
    text = response.text.strip()
    if len(text) > 500:
        text = f"{text[:500]}..."
    return text.replace("\n", " ")


def _extract_image(response_json: dict) -> tuple[str, str]:
    try:
        item = response_json["data"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("provider=openai_image message=missing image data in response") from exc

    if isinstance(item, dict):
        url = item.get("url")
        if isinstance(url, str) and url:
            return "url", url
        b64_json = item.get("b64_json")
        if isinstance(b64_json, str) and b64_json:
            return "b64_json", b64_json

    raise RuntimeError("provider=openai_image message=missing url or b64_json in response")


def _extract_chat_completions_image_url(response_json: dict) -> str:
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("provider=openai_image message=missing chat completion content in response") from exc

    if not isinstance(content, str):
        raise RuntimeError("provider=openai_image message=chat completion content is not text")

    match = re.search(r"!\[[^\]]*]\((https?://[^)\s]+)\)", content)
    if match:
        return match.group(1)

    raise RuntimeError("provider=openai_image message=missing markdown image url in chat completion response")


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _post_images_generations(
    *,
    session: requests.Session,
    base_url: str,
    api_key: str,
    payload: dict,
    timeout_seconds: int,
) -> tuple[str, str]:
    response = session.post(
        f"{base_url}/images/generations",
        headers=_headers(api_key),
        json=payload,
        timeout=(10, timeout_seconds),
    )
    if not response.ok:
        request_id = response.headers.get("X-Request-Id") or response.headers.get("x-request-id")
        raise RuntimeError(
            "provider=openai_image "
            f"mode=images_generations "
            f"message=request failed status={response.status_code} "
            f"request_id={request_id} response={_response_summary(response)}"
        )

    return _extract_image(response.json())


def _post_chat_completions(
    *,
    session: requests.Session,
    base_url: str,
    api_key: str,
    payload: dict,
    timeout_seconds: int,
) -> tuple[str, str]:
    response = session.post(
        f"{base_url}/chat/completions",
        headers=_headers(api_key),
        json=payload,
        timeout=(10, timeout_seconds),
    )
    if not response.ok:
        request_id = response.headers.get("X-Request-Id") or response.headers.get("x-request-id")
        raise RuntimeError(
            "provider=openai_image "
            f"mode=chat_completions "
            f"message=request failed status={response.status_code} "
            f"request_id={request_id} response={_response_summary(response)}"
        )

    return "url", _extract_chat_completions_image_url(response.json())


def generate(
    *,
    prompt_text: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str = "1:1",
    model: str | None = None,
    negative_prompt: str | None = None,
    prompt_extend: bool | None = None,
    watermark: bool | None = None,
) -> str:
    if reference_images:
        raise ValueError("provider=openai_image message=reference images are not supported yet")

    api_key = os.getenv("OPENAI_IMAGE_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "OPENAI_IMAGE_API_KEY is not set"

    selected_model = model or os.getenv("OPENAI_IMAGE_MODEL") or DEFAULT_MODEL
    base_url = (os.getenv("OPENAI_IMAGE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    mode = (os.getenv("OPENAI_IMAGE_MODE") or DEFAULT_MODE).strip().lower()
    timeout_seconds = int(
        os.getenv("OPENAI_IMAGE_TIMEOUT_SECONDS")
        or DEFAULT_TIMEOUT_SECONDS
    )

    session = _build_session()
    if mode == "images_generations":
        image_type, image_value = _post_images_generations(
            session=session,
            base_url=base_url,
            api_key=api_key,
            payload=_build_payload(
                prompt_text=prompt_text,
                model=selected_model,
                aspect_ratio=aspect_ratio,
            ),
            timeout_seconds=timeout_seconds,
        )
    elif mode == "chat_completions":
        image_type, image_value = _post_chat_completions(
            session=session,
            base_url=base_url,
            api_key=api_key,
            payload=_build_chat_completions_payload(
                prompt_text=prompt_text,
                model=selected_model,
                aspect_ratio=aspect_ratio,
            ),
            timeout_seconds=timeout_seconds,
        )
    else:
        raise ValueError(
            "provider=openai_image "
            f"message=unsupported OPENAI_IMAGE_MODE={mode}; expected images_generations or chat_completions"
        )

    if image_type == "url":
        _download_image(session, image_value, output_file)
    else:
        _write_b64_image(image_value, output_file)

    return f"Successfully generated image to {output_file} provider=openai_image model={selected_model} mode={mode}"
