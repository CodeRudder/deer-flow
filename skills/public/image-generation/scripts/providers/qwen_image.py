import io
import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image


DEFAULT_BASE_URL = "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1"
DEFAULT_MODEL = "qwen-image-2.0-pro"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_PROMPT_EXTEND = True
DEFAULT_WATERMARK = False
DEFAULT_NEGATIVE_PROMPT = (
    "低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，"
    "人脸无细节，过度光滑，画面具有AI感，构图混乱，文字模糊，扭曲。"
)


def _aspect_ratio_to_size(aspect_ratio: str) -> str:
    size_map = {
        "1:1": "2048*2048",
        "16:9": "2048*1152",
        "9:16": "1152*2048",
        "4:3": "2048*1536",
        "3:4": "1536*2048",
        "3:2": "2048*1360",
        "2:3": "1360*2048",
    }
    if aspect_ratio in size_map:
        return size_map[aspect_ratio]

    try:
        width_ratio, height_ratio = [float(part) for part in aspect_ratio.split(":", 1)]
    except ValueError as exc:
        raise ValueError(f"Invalid aspect ratio for qwen_image: {aspect_ratio}") from exc

    if width_ratio <= 0 or height_ratio <= 0:
        raise ValueError(f"Invalid aspect ratio for qwen_image: {aspect_ratio}")

    if width_ratio >= height_ratio:
        width = 2048
        height = int(width * height_ratio / width_ratio)
    else:
        height = 2048
        width = int(height * width_ratio / height_ratio)

    width = max(8, width - width % 8)
    height = max(8, height - height % 8)
    return f"{width}*{height}"


def _build_payload(
    *,
    prompt_text: str,
    model: str,
    aspect_ratio: str,
    negative_prompt: str | None,
    prompt_extend: bool | None,
    watermark: bool | None,
) -> dict:
    return {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt_text}],
                }
            ]
        },
        "parameters": {
            "negative_prompt": negative_prompt or DEFAULT_NEGATIVE_PROMPT,
            "prompt_extend": DEFAULT_PROMPT_EXTEND if prompt_extend is None else prompt_extend,
            "watermark": DEFAULT_WATERMARK if watermark is None else watermark,
            "size": _aspect_ratio_to_size(aspect_ratio),
        },
    }


def _extract_image_url(response_json: dict) -> str:
    try:
        content = response_json["output"]["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("provider=qwen_image message=missing image content in response") from exc

    for item in content:
        if isinstance(item, dict) and item.get("image"):
            return item["image"]
    raise RuntimeError("provider=qwen_image message=missing image url in response")


def _download_image(image_url: str, output_file: str) -> None:
    response = requests.get(image_url, timeout=(10, 60))
    if not response.ok:
        parsed = urlparse(image_url)
        raise RuntimeError(
            "provider=qwen_image "
            f"message=failed to download image host={parsed.netloc} status={response.status_code}"
        )

    image_bytes = response.content
    if not image_bytes:
        raise RuntimeError("provider=qwen_image message=downloaded image is empty")

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    suffix = output_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.convert("RGB").save(output_path, format="JPEG")
        return

    # Qwen returns image bytes from a signed URL. For PNG or unknown extensions,
    # keep the original bytes to avoid unnecessary re-encoding.
    output_path.write_bytes(image_bytes)


def _response_summary(response: requests.Response) -> str:
    text = response.text.strip()
    if len(text) > 500:
        text = f"{text[:500]}..."
    return text.replace("\n", " ")


def _inspection_failed_hint(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""

    if payload.get("code") != "DataInspectionFailed":
        return ""

    return (
        " hint=input text did not pass Qwen safety inspection; rewrite the prompt "
        "with more neutral wording, remove sensitive entities, real people, political, "
        "military, national-security, violent, sexual, or other restricted content, "
        "then retry"
    )


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
        raise ValueError("provider=qwen_image message=reference images are not supported yet")

    api_key = os.getenv("QWEN_IMAGE_API_KEY")
    if not api_key:
        return "QWEN_IMAGE_API_KEY is not set"

    selected_model = model or DEFAULT_MODEL
    base_url = (os.getenv("QWEN_IMAGE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    timeout_seconds = int(
        os.getenv("QWEN_IMAGE_TIMEOUT_SECONDS")
        or DEFAULT_TIMEOUT_SECONDS
    )
    payload = _build_payload(
        prompt_text=prompt_text,
        model=selected_model,
        aspect_ratio=aspect_ratio,
        negative_prompt=negative_prompt,
        prompt_extend=prompt_extend,
        watermark=watermark,
    )

    response = requests.post(
        f"{base_url}/services/aigc/multimodal-generation/generation",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=(10, timeout_seconds),
    )
    if not response.ok:
        request_id = response.headers.get("X-Request-Id") or response.headers.get("x-request-id")
        raise RuntimeError(
            "provider=qwen_image "
            f"message=request failed status={response.status_code} "
            f"request_id={request_id} response={_response_summary(response)}"
            f"{_inspection_failed_hint(response)}"
        )

    response_json = response.json()
    image_url = _extract_image_url(response_json)
    _download_image(image_url, output_file)

    request_id = response_json.get("request_id")
    return (
        f"Successfully generated image to {output_file} "
        f"provider=qwen_image model={selected_model} request_id={request_id}"
    )
