"""H3 image gateway adapter — text-to-image via frame extraction.

Wraps the self-hosted **H3 image gateway**: a zero-touch layer in front of the H3
audio+video service that generates a 4-second clip and returns a grabbed frame
as PNG. The gateway has its own auth token and its own async job API, so this is
an independent provider from the video-side `minimax_h3_sglang` adapter — do not
try to share configuration between them.

Six tiers across two families, exposed as model names (the model IS the gateway
mode). Order is the menu order, and the FIRST entry is what the UI selects when
the user has not pinned a model:

    h3-clip-std     balanced, 4 steps / 768p / 22 frames, ~25 s, 1344x768  ← default
    h3-clip-hq      high,     8 steps / 768p / 22 frames, ~40 s, 1344x768
    h3-frame-draft  preview,  4 steps / 256p / 4 s clip, ~9 s,   448x256
    h3-frame-fast   draft,    8 steps / 256p / 4 s clip, ~16 s,  448x256
    h3-frame-std    balanced, 4 steps / 768p / 4 s clip, ~115 s, 1344x768
    h3-frame-hq     high,     8 steps / 768p / 4 s clip, ~225 s, 1344x768

The `clip` family renders only the shortest decodable fragment (22 frames ≈
0.92 s) and grabs a frame from it. Compute scales with latent frames, so it
produces the same 768p frame as the `frame` tiers at a fraction of the cost —
hence the `clip` family leads. The `frame` std/hq tiers are kept only for the
rare case where `frame_policy` must range across a wider 4-second span.

Image-to-image (`h3-i2i-*`) is a separate pair of tiers at the end: they REQUIRE
one reference image, which seeds the clip as its first frame, and default to
grabbing the LAST frame (`frame_policy=last`) — the evolved result. The gateway
rejects `reference_image` on every text-to-image tier, so this adapter gates both
directions locally rather than paying for a 400.

    h3-i2i-std      balanced, 4 steps / 768p / 22 frames + reference, ~31 s
    h3-i2i-hq       high,     8 steps / 768p / 22 frames + reference, ~50 s

Their semantics are generative evolution / reference-guided redraw (style,
background, pose) — they do NOT promise that unedited regions stay
pixel-identical. That is why they belong to this skill (reference as loose
inspiration) rather than image-editing, whose contract is structure preservation.

The gateway shares one GPU and one serial queue with the video API, and is
**video-first**: while the engine runs a video job, an image request yields
rather than competing for the slot, so a call can sit far longer than its tier
suggests. Contract verified against
projects/llm/minimax-h3/docs/integration/image-api-guide.md (2026-10-07).
"""

import base64
import hashlib
import json
import os
import time
from pathlib import Path

import requests

DEFAULT_BASE_URL = "http://100.108.144.120:8000"
DEFAULT_MODE = "h3-clip-std"
# Mirrors GET /v1/images/modes, in menu order: the clip family first (same
# frames, far cheaper), the slower 4s-video tiers last. The gateway's own
# validation is the backstop; this list only buys a fast local error.
KNOWN_MODES = (
    "h3-clip-std",
    "h3-clip-hq",
    "h3-frame-draft",
    "h3-frame-fast",
    "h3-frame-std",
    "h3-frame-hq",
    "h3-i2i-std",
    "h3-i2i-hq",
)

# These tiers REQUIRE a reference image; every other tier REJECTS one.
I2I_MODES = frozenset({"h3-i2i-std", "h3-i2i-hq"})

# The gateway's own cap on the original reference image.
REFERENCE_IMAGE_MAX_BYTES = 12 * 1024 * 1024

DEFAULT_POLL_INTERVAL_SECONDS = 5.0
# The sandbox runs each bash command with timeout=600 (local_sandbox.py), which
# caps the whole generate.py call. Give up BEFORE that so the caller gets an
# actionable error rather than a bare sandbox kill. Nothing is lost: re-running
# the same command re-attaches to the same job via Idempotency-Key, and
# artifacts persist gateway-side either way.
DEFAULT_POLL_TIMEOUT_SECONDS = 480.0
SUBMIT_TIMEOUT_SECONDS = 60
POLL_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = (10, 120)

SHORT_EDGE_MIN = 128
SHORT_EDGE_MAX = 2048
_FRAME_POLICIES = ("first", "last", "middle")

# Params the gateway does not implement. Passing them is not an error, but
# silently dropping them would let a caller believe they took effect.
_UNSUPPORTED_PARAMS = ("negative_prompt", "prompt_extend", "watermark")

# The gateway reports a yield timeout with this phrase; it is a transient
# "engine is busy with video", not a defect in the request.
_VIDEO_YIELD_MARKER = "engine busy with video jobs"


def _base_url() -> str:
    return os.getenv("H3_IMAGE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _auth_token() -> str | None:
    # The gateway's own env name is accepted as a fallback so an operator can
    # copy the token straight out of ~/h3img-gateway/.env without renaming it.
    return os.getenv("H3_IMAGE_AUTH_TOKEN") or os.getenv("H3IMG_AUTH_TOKEN")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, *, minimum: int, maximum: int) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _seed_env() -> int | None:
    return _int_env("H3_IMAGE_SEED", minimum=0, maximum=2**31 - 1)


def _frame_policy_env() -> str | None:
    raw = os.getenv("H3_IMAGE_FRAME_POLICY")
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if value in _FRAME_POLICIES:
        return value
    if value.startswith("at:"):
        try:
            position = float(value[3:])
        except ValueError:
            position = -1.0
        if 0.0 <= position <= 1.0:
            return value
    raise ValueError(
        f"H3_IMAGE_FRAME_POLICY must be one of {', '.join(_FRAME_POLICIES)} "
        f"or 'at:<0..1>', got {raw!r}"
    )


def _reference_format(head: bytes) -> str | None:
    """Sniff the container the gateway accepts, so a bad file fails locally."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WebP"
    return None


def _reference_frame_index_env() -> int | None:
    raw = os.getenv("H3_IMAGE_REFERENCE_FRAME_INDEX")
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if value not in ("0", "-1"):
        raise ValueError(
            f"H3_IMAGE_REFERENCE_FRAME_INDEX must be 0 (reference seeds the first frame) "
            f"or -1 (it seeds the last frame), got {raw!r}"
        )
    return int(value)


def _load_reference_image(path: str) -> str:
    reference = Path(path)
    try:
        raw = reference.read_bytes()
    except OSError as exc:
        raise ValueError(
            f"provider=h3_image message=cannot read reference image {path!r}: {exc}"
        ) from exc
    if len(raw) > REFERENCE_IMAGE_MAX_BYTES:
        raise ValueError(
            f"provider=h3_image message=reference image is too large: {len(raw)} bytes "
            f"(limit {REFERENCE_IMAGE_MAX_BYTES})"
        )
    if _reference_format(raw[:16]) is None:
        raise ValueError(
            f"provider=h3_image message=unsupported reference image format for {path!r}; "
            f"the gateway accepts PNG, JPEG, or WebP"
        )
    # The gateway accepts a bare payload or a data: prefix — send it bare.
    return base64.b64encode(raw).decode()


def _idempotency_key(base: str, body: dict) -> str | None:
    """Derive a stable key so a retry re-attaches instead of re-running the GPU.

    The gateway returns the original job for the same key + same body within
    24 h. That makes an automatic retry after a timeout free, which matters on a
    single-card queue shared with video. The flip side is that a *deliberate*
    re-roll must opt out, or it would keep getting the same image back — hence
    `H3_IMAGE_NO_IDEMPOTENCY`.
    """
    if (os.getenv("H3_IMAGE_NO_IDEMPOTENCY") or "").strip() not in ("", "0"):
        return None
    explicit = (os.getenv("H3_IMAGE_IDEMPOTENCY_KEY") or "").strip()
    if explicit:
        return explicit
    material = json.dumps(
        {"base": base, "body": body}, sort_keys=True, ensure_ascii=False
    )
    return "h3img-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _error_detail(resp) -> tuple[str, str, object]:
    try:
        err = (resp.json() or {}).get("error")
    except ValueError:
        err = None
    if not isinstance(err, dict):
        return "http_error", (getattr(resp, "text", "") or "")[:500], None
    return err.get("code", "http_error"), err.get("message", ""), err.get("retryable")


def _raise_for_error(resp, operation: str) -> None:
    if resp.status_code < 400:
        return
    code, message, retryable = _error_detail(resp)
    detail = (
        f"provider=h3_image {operation}: HTTP {resp.status_code} "
        f"{code}: {message} (retryable={retryable})"
    )
    retry_after = (getattr(resp, "headers", None) or {}).get("Retry-After")
    if retry_after:
        detail += f" Retry-After: {retry_after}"
        if retryable:
            detail += " — not queued; retrying after that delay is safe"
    raise RuntimeError(detail)


def _poll_until_done(base: str, headers: dict, job_id: str) -> dict:
    interval = _float_env(
        "H3_IMAGE_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS
    )
    budget = _float_env("H3_IMAGE_POLL_TIMEOUT_SECONDS", DEFAULT_POLL_TIMEOUT_SECONDS)
    deadline = time.monotonic() + budget
    last_note = None

    while True:
        resp = requests.get(
            f"{base}/v1/images/jobs/{job_id}",
            headers=headers,
            timeout=POLL_TIMEOUT_SECONDS,
        )
        _raise_for_error(resp, "poll")
        payload = resp.json()
        status = payload.get("status")

        # The gateway narrates long waits here (e.g. "yielding: 2 video
        # job(s) on engine"); surfacing it keeps a slow job from looking hung.
        note = payload.get("note")
        if note and note != last_note:
            print(f"[h3_image] job={job_id} {status}: {note}")
            last_note = note

        if status == "completed":
            return payload
        if status in ("failed", "cancelled"):
            err = payload.get("error") or {}
            message = err.get("message", "")
            detail = (
                f"provider=h3_image job {job_id} {status}: "
                f"{err.get('code', 'unknown')}: {message} (retryable={err.get('retryable')})"
            )
            if _VIDEO_YIELD_MARKER in message:
                detail += (
                    " — the engine was busy with video jobs and the gateway gave up waiting; "
                    "this is transient, wait a moment and retry"
                )
            raise RuntimeError(detail)
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"provider=h3_image job {job_id} still '{status}' after {budget:.0f}s; "
                f"it may still finish — re-run the SAME command to re-attach to this job "
                f"(the Idempotency-Key returns the same job, so no extra GPU run), "
                f"or fetch it directly: GET {base}/v1/images/jobs/{job_id}/content?variant=0"
            )
        time.sleep(interval)


def _actual_seed(payload: dict) -> object:
    data = payload.get("data") or []
    if not data:
        return None
    engine = ((data[0] or {}).get("meta") or {}).get("engine") or {}
    return engine.get("seed")


def _write_image(payload: dict, base: str, headers: dict, output_file: str) -> None:
    data = payload.get("data") or []
    if not data:
        raise RuntimeError("provider=h3_image message=job completed without image data")
    first = data[0] or {}

    encoded = first.get("b64_json")
    if encoded:
        image_bytes = base64.b64decode(encoded)
    else:
        url = first.get("url")
        if not url:
            raise RuntimeError(
                "provider=h3_image message=job completed but carries neither b64_json nor url"
            )
        if url.startswith("/"):
            url = base + url
        resp = requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"provider=h3_image message=failed to download image: HTTP {resp.status_code}"
            )
        image_bytes = resp.content

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)


def _build_body(
    prompt_text: str, mode: str, aspect_ratio: str, reference_images: list[str]
) -> dict:
    """Assemble the request body, validating the optional overrides locally.

    Validating before the POST turns a typo into an immediate error instead of a
    round trip that the gateway would reject (or silently ignore) — which matters
    on a queue shared with video.
    """
    body = {
        "prompt": prompt_text,
        "mode": mode,
        "aspect_ratio": aspect_ratio,
        "wait": False,
    }
    seed = _seed_env()
    if seed is not None:
        body["seed"] = seed
    short_edge = _int_env(
        "H3_IMAGE_SHORT_EDGE", minimum=SHORT_EDGE_MIN, maximum=SHORT_EDGE_MAX
    )
    if short_edge is not None:
        body["short_edge"] = short_edge
    frame_policy = _frame_policy_env()
    if frame_policy is not None:
        body["frame_policy"] = frame_policy
    frame_index = _reference_frame_index_env()
    if mode in I2I_MODES:
        body["reference_image"] = _load_reference_image(reference_images[0])
        if frame_index is not None:
            body["reference_frame_index"] = frame_index
    return body


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
    token = _auth_token()
    if not token:
        return "H3_IMAGE_AUTH_TOKEN is not set"

    mode = model or os.getenv("H3_IMAGE_MODEL") or DEFAULT_MODE
    if mode not in KNOWN_MODES:
        raise ValueError(
            f"provider=h3_image message=unknown h3_image mode '{mode}'; "
            f"known modes: {', '.join(KNOWN_MODES)} (see GET /v1/images/modes)"
        )

    # The gateway requires a reference on the i2i tiers and rejects one
    # everywhere else; gate both directions here so neither costs a round trip.
    if mode in I2I_MODES:
        if not reference_images:
            raise ValueError(
                f"provider=h3_image message=mode '{mode}' requires a reference image "
                f"(the h3-i2i-* tiers are image-to-image; pass --reference-images)"
            )
        if len(reference_images) > 1:
            raise ValueError(
                f"provider=h3_image message=h3_image takes exactly one reference image, "
                f"got {len(reference_images)}"
            )
    elif reference_images:
        raise ValueError(
            f"provider=h3_image message=mode '{mode}' is text-to-image and does not accept "
            f"reference images; use an h3-i2i-* mode for image-to-image"
        )

    supplied = {
        "negative_prompt": negative_prompt,
        "prompt_extend": prompt_extend,
        "watermark": watermark,
    }
    for name in _UNSUPPORTED_PARAMS:
        if supplied[name] is not None:
            print(
                f"Warning: provider=h3_image ignores '{name}' "
                f"(the H3 image gateway does not implement it)"
            )

    base = _base_url()
    headers = _headers(token)
    body = _build_body(prompt_text, mode, aspect_ratio, reference_images)

    # Submit only, then poll here: each request stays short, and we can print
    # progress and bound the wait ourselves instead of relying on `wait: true`.
    key = _idempotency_key(base, body)
    submit_headers = {**headers, "Content-Type": "application/json"}
    if key:
        submit_headers["Idempotency-Key"] = key

    resp = requests.post(
        f"{base}/v1/images/generations",
        headers=submit_headers,
        json=body,
        timeout=SUBMIT_TIMEOUT_SECONDS,
    )
    _raise_for_error(resp, "submit")
    job_id = (resp.json() or {}).get("id")
    if not job_id:
        raise RuntimeError(
            f"provider=h3_image message=no job id in submit response: {resp.json()}"
        )

    print(f"[h3_image] submitted job={job_id} mode={mode} aspect_ratio={aspect_ratio}")
    payload = _poll_until_done(base, headers, job_id)
    _write_image(payload, base, headers, output_file)

    seed = _actual_seed(payload)
    seed_suffix = f" seed={seed}" if seed is not None else ""
    print(f"[h3_image] completed job={job_id} mode={mode}{seed_suffix}")
    return (
        f"Successfully generated image to {output_file} "
        f"provider=h3_image model={mode} job={job_id}{seed_suffix}"
    )
