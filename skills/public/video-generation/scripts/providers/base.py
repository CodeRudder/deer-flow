"""Async three-step video-generation template shared by all providers.

Every provider is "create task -> poll until terminal -> fetch video URL ->
download". Differences live in four hooks: auth, create payload/parse, poll +
status normalization, and video-URL extraction (H3 V2 takes it directly; the
legacy MiniMax V1 needs an extra files/retrieve step). `BaseVideoProvider.generate` owns the loop; adapters only
implement the differences.
"""

import base64
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

# Normalized task states. Adapters translate their own vendor status strings
# into one of these so the poll loop stays provider-agnostic.
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"
STATUS_PROVIDER_SUCCEEDED = "provider_succeeded"

_BILLING_ENV_FIELDS = {
    "reservation_id": "DEERFLOW_VIDEO_QUOTA_RESERVATION_ID",
    "usage_period_id": "DEERFLOW_VIDEO_QUOTA_USAGE_PERIOD_ID",
    "record_id": "DEERFLOW_VIDEO_QUOTA_RECORD_ID",
    "run_id": "DEERFLOW_VIDEO_QUOTA_RUN_ID",
    "thread_id": "DEERFLOW_VIDEO_QUOTA_THREAD_ID",
    "idempotency_key": "DEERFLOW_VIDEO_QUOTA_IDEMPOTENCY_KEY",
}

_MIME_BY_EXT = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


def guess_mime(image_path: str) -> str:
    return _MIME_BY_EXT.get(os.path.splitext(image_path)[1].lower(), "image/jpeg")


def to_data_url(image_path: str) -> str:
    """base64 data URL for a local image; public URLs should be passed through."""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{guess_mime(image_path)};base64,{b64}"


def image_ref(image: str) -> str:
    """Public URL is sent as-is (preferred for large media); local path -> data URL."""
    if image.startswith(("http://", "https://")):
        return image
    return to_data_url(image)


def video_ref(video_path: str) -> str:
    """Public URL is sent as-is (preferred); local path -> base64 data URL.
    Base64 inflates ~33% against the 64 MB request-body cap, so large sources
    should be hosted at a public URL instead."""
    if video_path.startswith(("http://", "https://")):
        return video_path
    with open(video_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:video/mp4;base64,{b64}"


def ensure_output_dir(output_file: str) -> None:
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)


def task_record_path(output_file: str) -> Path:
    return Path(output_file).with_suffix(".task.json")


def write_task_record(output_file: str, record: dict) -> None:
    """Sidecar `.task.json` next to the output so a later run (or --cancel) can
    discover the task instead of double-submitting."""
    path = task_record_path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def read_task_record(output_file: str) -> dict | None:
    """Read a task sidecar, returning None when it is missing or invalid."""
    path = task_record_path(output_file)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def update_task_record(output_file: str, task_id: str, **updates) -> bool:
    """Update a matching task sidecar while preserving its other metadata."""
    record = read_task_record(output_file)
    if record is None:
        return False
    if record.get("task_id") != task_id:
        return False
    record.update(updates)
    record["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    write_task_record(output_file, record)
    return True


def set_task_status(output_file: str, task_id: str, status: str) -> bool:
    """Update the sidecar's status when it matches task_id; no-op otherwise."""
    return update_task_record(output_file, task_id, status=status)


def billing_context() -> dict:
    """Return non-secret quota identifiers injected by the runtime bridge."""
    return {
        field: value
        for field, env_name in _BILLING_ENV_FIELDS.items()
        if (value := os.getenv(env_name))
    }


def billing_metadata(model: str | None, params: dict) -> dict:
    """Copy request billing inputs into the sidecar for runtime validation."""
    context = billing_context()
    metadata = {
        "record_id": context.get("record_id"),
        "model": model,
        "resolution": params.get("resolution"),
        "requested_duration_seconds": params.get("duration"),
        "billable_duration_seconds": params.get("duration"),
    }
    return {key: value for key, value in metadata.items() if value is not None}


def warn_ignored(provider: str, params: dict, supported: set[str]) -> None:
    """Surface params a provider does not honor instead of silently dropping them (AC-11)."""
    ignored = [k for k, v in params.items() if v is not None and k not in supported]
    if ignored:
        print(f"Warning: provider={provider} ignores unsupported params: {', '.join(sorted(ignored))}")


class BaseVideoProvider:
    """Async three-step template. Subclasses set `name` and override the hooks."""

    name = "base"
    # Params this adapter honors; the rest trigger warn_ignored.
    supported_params: set[str] = set()
    # Env vars accepted as credential; used by the missing-key error message.
    api_key_envs: tuple[str, ...] = ()
    # Every model this adapter serves, not just default_model. Feeds
    # MODEL_PROVIDERS so credential fallback routes each model to its owner.
    known_models: tuple[str, ...] = ()
    # Poll cadence — adapters override (H3 recommends 10s; V1 uses 3s).
    poll_interval = 10
    poll_max_attempts = 120

    def __init__(self, model: str | None = None):
        self.model = model

    # --- hooks: subclasses implement the differences ---

    def api_key(self) -> str | None:
        """Return the provider credential from the environment, or None if unset."""
        raise NotImplementedError

    def auth_headers(self) -> dict:
        """Auth header(s). Bearer for the mainline providers."""
        return {"Authorization": f"Bearer {self.api_key()}"}

    def create_task(self, prompt_text: str, reference_images: list[str], params: dict) -> str:
        """Create the async task; return an opaque handle (task_id or operation name).

        Adapters decide how to use reference_images: H3/V1 take the first as the
        first frame.
        """
        raise NotImplementedError

    def poll_once(self, handle: str) -> tuple[str, dict]:
        """Poll once; return (normalized_status, raw_result)."""
        raise NotImplementedError

    def extract_video_url(self, handle: str, result: dict) -> str:
        """Pull the downloadable video URL out of a succeeded poll result."""
        raise NotImplementedError

    def cancel(self, handle: str, output_file: str | None = None) -> str:
        """Cancel a queued task; returns a human-readable result. Implementers
        must check task state first — MiniMax's DELETE both cancels queued
        tasks and deletes finished records, and only the former may happen
        here (finished records feed regeneration/audit lookups)."""
        raise NotImplementedError(f"provider={self.name} does not support --cancel")

    def download(self, url: str, output_file: str) -> None:
        """Default: plain GET."""
        resp = requests.get(url, timeout=300)
        resp.raise_for_status()
        ensure_output_dir(output_file)
        with open(output_file, "wb") as f:
            f.write(resp.content)

    # --- template: owns the create -> poll -> extract -> download loop ---

    def generate(
        self,
        prompt_text: str,
        reference_images: list[str],
        output_file: str,
        params: dict,
        max_attempts: int | None = None,
        interval: int | None = None,
        prompt_file: str | None = None,
    ) -> str:
        if not self.api_key():
            hint = f"; set one of: {', '.join(self.api_key_envs)}" if self.api_key_envs else ""
            raise Exception(f"provider={self.name} credential is not set{hint}")

        max_attempts = max_attempts if max_attempts is not None else self.poll_max_attempts
        interval = interval if interval is not None else self.poll_interval
        warn_ignored(self.name, params, self.supported_params)

        # Persist the reservation before contacting the provider. If task
        # creation fails without a task id, the runtime releases the points.
        write_task_record(
            output_file,
            {
                "schema_version": 2,
                **billing_context(),
                "provider": self.name,
                "task_id": None,
                "model": self.model,
                "prompt_file": prompt_file,
                "output_file": output_file,
                "params": params,
                "billing": billing_metadata(self.model, params),
                "status": STATUS_PENDING,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        try:
            handle = self.create_task(prompt_text, reference_images, params)
        except ValueError:
            # Local validation proves the Provider was never called, so the
            # runtime can release the reservation instead of leaving it pending.
            set_task_status(output_file, None, "rejected")
            raise
        print(f"[create] provider={self.name} handle={handle}")
        record = read_task_record(output_file) or {}
        record.update(task_id=handle, updated_at=datetime.now(UTC).isoformat(timespec="seconds"))
        write_task_record(output_file, record)

        started = time.monotonic()
        for attempt in range(max_attempts):
            status, result = self.poll_once(handle)
            if status == STATUS_SUCCEEDED:
                url = self.extract_video_url(handle, result)
                print(f"[poll] succeeded after {attempt + 1} polls")
                set_task_status(output_file, handle, STATUS_PROVIDER_SUCCEEDED)
                self.download(url, output_file)
                set_task_status(output_file, handle, STATUS_SUCCEEDED)
                return f"The video has been generated successfully to {output_file}"
            if status == STATUS_FAILED:
                set_task_status(output_file, handle, STATUS_FAILED)
                raise Exception(f"provider={self.name} task {handle} failed: {result}")
            print(f"[poll] attempt {attempt + 1}: status=pending ({time.monotonic() - started:.0f}s elapsed)")
            time.sleep(interval)
        set_task_status(output_file, handle, "timeout")
        raise Exception(f"provider={self.name} task {handle} timed out after {max_attempts} polls")
