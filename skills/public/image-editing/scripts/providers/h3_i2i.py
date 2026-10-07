"""H3 image gateway adapter for image editing — image-to-image.

Drives the self-hosted **H3 image gateway** in its image-to-image mode: the
supplied image seeds a short clip as its first frame and the gateway returns a
later frame of that clip, i.e. the image evolved by the prompt. It is the only
editing provider that needs no external credential — it authenticates with the
same token the image-generation side already uses.

Two tiers, named by the gateway mode:

    h3-i2i-std   4 steps / 768p / 22 frames, ~31 s
    h3-i2i-hq    8 steps / 768p / 22 frames, ~50 s   ← default

**Boundary, and it matters for routing.** This is *generative evolution /
reference-guided redraw*: style, background and pose change as instructed, and
the subject is usually recognisable, but the gateway does **not** promise that
unedited regions stay pixel-identical, and there is no mask/inpainting support.
Structure-preserving work (a design drawing or blueprint turned into a product
photo, "recolor this part and leave the rest untouched") needs a provider that
honours that contract; this one cannot.

edit.py gates every provider on a non-empty ``Authorization`` from config.yaml.
This provider builds its own ``Bearer`` header from the H3 token env instead, so
it opts out of that gate via ``edit.REQUIRES_AUTHORIZATION = False`` below.
"""

import base64
import hashlib
import json
import os
import time
from pathlib import Path

import requests

DEFAULT_BASE_URL = "http://100.108.144.120:8000"
DEFAULT_MODE = "h3-i2i-hq"
# The gateway's image-to-image tiers; anything else is text-to-image and would
# reject the reference image outright.
KNOWN_MODES = ("h3-i2i-std", "h3-i2i-hq")

DEFAULT_POLL_INTERVAL_SECONDS = 5.0
SUBMIT_TIMEOUT_SECONDS = 60
POLL_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = (10, 120)

REFERENCE_IMAGE_MAX_BYTES = 12 * 1024 * 1024

# The gateway reports a yield timeout with this phrase; it is a transient
# "engine is busy with video", not a defect in the request.
_VIDEO_YIELD_MARKER = "engine busy with video jobs"


def _auth_token() -> str | None:
    # The gateway's own env name is accepted as a fallback so an operator can
    # copy the token straight out of ~/h3img-gateway/.env without renaming it.
    return os.getenv("H3_IMAGE_AUTH_TOKEN") or os.getenv("H3IMG_AUTH_TOKEN")


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _reference_format(head: bytes) -> str | None:
    """Sniff the container the gateway accepts, so a bad file fails locally."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WebP"
    return None


def _load_reference_image(path: str) -> tuple[str, str]:
    """Return the base64 payload and a digest, so retries derive a stable key."""
    image = Path(path)
    try:
        raw = image.read_bytes()
    except OSError as exc:
        raise ValueError(f"provider=h3_i2i message=cannot read reference image {path!r}: {exc}") from exc
    if len(raw) > REFERENCE_IMAGE_MAX_BYTES:
        raise ValueError(
            f"provider=h3_i2i message=reference image is too large: {len(raw)} bytes "
            f"(limit {REFERENCE_IMAGE_MAX_BYTES})"
        )
    if _reference_format(raw[:16]) is None:
        raise ValueError(
            f"provider=h3_i2i message=unsupported reference image format for {path!r}; "
            f"the gateway accepts PNG, JPEG, or WebP"
        )
    # The gateway accepts a bare payload or a data: prefix — send it bare.
    return base64.b64encode(raw).decode(), hashlib.sha256(raw).hexdigest()[:16]


def _idempotency_key(base: str, mode: str, prompt_text: str, digest: str) -> str | None:
    """A retry after a timeout re-attaches instead of re-running the GPU.

    The gateway returns the original job for the same key + body within 24 h,
    which matters on the single-card queue this shares with video.
    """
    if (os.getenv("H3_IMAGE_NO_IDEMPOTENCY") or "").strip() not in ("", "0"):
        return None
    material = json.dumps(
        {"base": base, "mode": mode, "prompt": prompt_text, "image": digest},
        sort_keys=True,
        ensure_ascii=False,
    )
    return "h3i2i-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _raise_for_error(response, operation: str) -> None:
    if response.status_code < 400:
        return
    try:
        error = (response.json() or {}).get("error")
    except ValueError:
        error = None
    if isinstance(error, dict):
        code = error.get("code", "http_error")
        message = error.get("message", "")
        retryable = error.get("retryable")
    else:
        code, message, retryable = "http_error", (response.text or "")[:500], None
    detail = f"provider=h3_i2i {operation}: HTTP {response.status_code} {code}: {message} (retryable={retryable})"
    retry_after = (getattr(response, "headers", None) or {}).get("Retry-After")
    if retry_after:
        detail += f" Retry-After: {retry_after}"
        if retryable:
            detail += " — not queued; retrying after that delay is safe"
    raise RuntimeError(detail)


def _poll_until_done(base: str, headers: dict, job_id: str, budget: float) -> dict:
    interval = _float_env("H3_IMAGE_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
    deadline = time.monotonic() + budget
    last_note = None

    while True:
        response = requests.get(
            f"{base}/v1/images/jobs/{job_id}",
            headers=headers,
            timeout=POLL_TIMEOUT_SECONDS,
        )
        _raise_for_error(response, "poll")
        payload = response.json()
        status = payload.get("status")

        # The gateway narrates long waits here (e.g. "yielding: 2 video job(s)
        # on engine"); surfacing it keeps a slow job from looking hung.
        note = payload.get("note")
        if note and note != last_note:
            print(f"[h3_i2i] job={job_id} {status}: {note}")
            last_note = note

        if status == "completed":
            return payload
        if status in ("failed", "cancelled"):
            error = payload.get("error") or {}
            message = error.get("message", "")
            detail = (
                f"provider=h3_i2i job {job_id} {status}: "
                f"{error.get('code', 'unknown')}: {message} (retryable={error.get('retryable')})"
            )
            if _VIDEO_YIELD_MARKER in message:
                detail += (
                    " — the engine was busy with video jobs and the gateway gave up waiting; "
                    "this is transient, wait a moment and retry"
                )
            raise RuntimeError(detail)
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"provider=h3_i2i job {job_id} still '{status}' after {budget:.0f}s; "
                f"it may still finish — re-run the SAME command to re-attach to this job "
                f"(the Idempotency-Key returns the same job, so no extra GPU run), "
                f"or fetch it directly: GET {base}/v1/images/jobs/{job_id}/content?variant=0"
            )
        time.sleep(interval)


def _write_image(payload: dict, base: str, headers: dict, output_file: str) -> None:
    data = payload.get("data") or []
    if not data:
        raise RuntimeError("provider=h3_i2i message=job completed without image data")
    first = data[0] or {}

    encoded = first.get("b64_json")
    if encoded:
        image_bytes = base64.b64decode(encoded)
    else:
        url = first.get("url")
        if not url:
            raise RuntimeError("provider=h3_i2i message=job completed but carries neither b64_json nor url")
        if url.startswith("/"):
            url = base + url
        response = requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        if response.status_code >= 400:
            raise RuntimeError(f"provider=h3_i2i message=failed to download image: HTTP {response.status_code}")
        image_bytes = response.content

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)


def edit(
    *,
    prompt_text: str,
    reference_images: list[str],
    output_file: str,
    authorization: str,
    base_url: str,
    timeout_seconds: int = 300,
    size: str = "auto",
    model: str | None = None,
    quality: str = "high",
    output_format: str = "png",
    api_version: str | None = None,
    mask: str | None = None,
) -> str:
    # The signature mirrors the other providers so edit.py can dispatch to it;
    # the OpenAI-only knobs are accepted and ignored.
    if mask:
        raise ValueError("mask is not supported yet")

    token = _auth_token()
    if not token:
        return "H3_IMAGE_AUTH_TOKEN is not set"

    if len(reference_images) != 1:
        raise ValueError(
            f"provider=h3_i2i message=image-to-image takes exactly one reference image, "
            f"got {len(reference_images)}"
        )

    for name, value, default in (
        ("size", size, "auto"),
        ("quality", quality, "high"),
        ("output_format", output_format, "png"),
        ("api_version", api_version, None),
    ):
        if value != default:
            print(f"Warning: provider=h3_i2i ignores '{name}' (the H3 gateway does not implement it)")

    mode = model or os.getenv("H3_IMAGE_MODEL") or DEFAULT_MODE
    if mode not in KNOWN_MODES:
        raise ValueError(
            f"provider=h3_i2i message=unknown h3_i2i model '{mode}'; "
            f"known models: {', '.join(KNOWN_MODES)} (see GET /v1/images/modes)"
        )

    base = (base_url or os.getenv("H3_IMAGE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    reference_b64, digest = _load_reference_image(reference_images[0])

    body = {
        "prompt": prompt_text,
        "mode": mode,
        "reference_image": reference_b64,
        "wait": False,
    }
    submit_headers = {**headers, "Content-Type": "application/json"}
    key = _idempotency_key(base, mode, prompt_text, digest)
    if key:
        submit_headers["Idempotency-Key"] = key

    response = requests.post(
        f"{base}/v1/images/generations",
        headers=submit_headers,
        json=body,
        timeout=SUBMIT_TIMEOUT_SECONDS,
    )
    _raise_for_error(response, "submit")
    job_id = (response.json() or {}).get("id")
    if not job_id:
        raise RuntimeError(f"provider=h3_i2i message=no job id in submit response: {response.json()}")

    print(f"[h3_i2i] submitted job={job_id} mode={mode}")
    # The shared `timeout` from config.yaml becomes the poll budget; the env var
    # overrides it. Both stay under the 600 s sandbox ceiling.
    budget = _float_env("H3_IMAGE_POLL_TIMEOUT_SECONDS", float(timeout_seconds))
    payload = _poll_until_done(base, headers, job_id, budget)
    _write_image(payload, base, headers, output_file)

    seed = (((payload.get("data") or [{}])[0] or {}).get("meta") or {}).get("engine", {}).get("seed")
    seed_suffix = f" seed={seed}" if seed is not None else ""
    print(f"[h3_i2i] completed job={job_id} mode={mode}{seed_suffix}")
    return (
        f"Successfully edited image to {output_file} provider=h3_i2i "
        f"model={mode} job={job_id}{seed_suffix}"
    )


# edit.py gates every provider on a non-empty Authorization from config.yaml,
# because the OpenAI-style providers need one. This provider derives its header
# from the shared H3 token env instead, so it opts out of that gate. The flag
# lives on the callable itself because that is what edit.py dispatches to.
edit.REQUIRES_AUTHORIZATION = False