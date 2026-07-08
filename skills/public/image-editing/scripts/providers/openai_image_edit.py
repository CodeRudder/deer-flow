import base64
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image


DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "high"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-2"


def _response_summary(response: requests.Response) -> str:
    text = response.text.strip()
    if len(text) > 500:
        text = f"{text[:500]}..."
    return text.replace("\n", " ")


def _build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _download_image(session: requests.Session, image_url: str, output_file: str) -> None:
    response = session.get(image_url, timeout=(10, 60))
    if not response.ok:
        parsed = urlparse(image_url)
        raise RuntimeError(
            "provider=openai_image_edit "
            f"message=failed to download image host={parsed.netloc} status={response.status_code}"
        )
    if not response.content:
        raise RuntimeError("provider=openai_image_edit message=downloaded image is empty")

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)


def _write_b64_image(b64_json: str, output_file: str) -> None:
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(base64.b64decode(b64_json))


def _extract_image(response_json: dict) -> tuple[str, str]:
    try:
        item = response_json["data"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("provider=openai_image_edit message=missing image data in response") from exc

    if isinstance(item, dict):
        url = item.get("url")
        if isinstance(url, str) and url:
            return "url", url
        b64_json = item.get("b64_json")
        if isinstance(b64_json, str) and b64_json:
            return "b64_json", b64_json

    raise RuntimeError("provider=openai_image_edit message=missing url or b64_json in response")


def edit(
    *,
    prompt_text: str,
    reference_images: list[str],
    output_file: str,
    authorization: str,
    base_url: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    size: str = DEFAULT_SIZE,
    model: str | None = None,
    quality: str = DEFAULT_QUALITY,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    api_version: str | None = None,
    mask: str | None = None,
) -> str:
    if mask:
        raise ValueError("mask is not supported yet")

    if not reference_images:
        raise ValueError("provider=openai_image_edit message=at least one input image is required")

    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for image_path in reference_images:
        with Image.open(image_path) as image:
            image.verify()
        content = Path(image_path).read_bytes()
        suffix = Path(image_path).suffix.lower().lstrip(".") or "png"
        mime_type = "image/png" if suffix == "png" else f"image/{suffix}"
        files.append(("image[]", (Path(image_path).name, content, mime_type)))

    data = {
        "prompt": prompt_text,
        "size": size,
        "quality": quality,
        "output_format": output_format,
    }
    selected_model = model or DEFAULT_MODEL
    if selected_model:
        data["model"] = selected_model

    session = _build_session()
    response = session.post(
        f"{base_url.rstrip('/')}/images/edits",
        headers={"Authorization": authorization},
        data=data,
        files=files,
        params={"api-version": api_version} if api_version else None,
        timeout=(10, timeout_seconds),
    )
    if not response.ok:
        request_id = response.headers.get("X-Request-Id") or response.headers.get("x-request-id")
        raise RuntimeError(
            "provider=openai_image_edit "
            f"message=request failed status={response.status_code} "
            f"request_id={request_id} response={_response_summary(response)}"
        )

    image_type, image_value = _extract_image(response.json())
    if image_type == "url":
        _download_image(session, image_value, output_file)
    else:
        _write_b64_image(image_value, output_file)

    return (
        f"Successfully edited image to {output_file} "
        f"provider=openai_image_edit model={selected_model}"
    )
