import asyncio
import hashlib
import logging
import mimetypes
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app.gateway.authz import require_permission
from app.gateway.path_utils import resolve_thread_virtual_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["artifacts"])

ACTIVE_CONTENT_MIME_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
}

MAX_SKILL_ARCHIVE_MEMBER_BYTES = 16 * 1024 * 1024
_SKILL_ARCHIVE_READ_CHUNK_SIZE = 64 * 1024

# --- Scaled image variants -------------------------------------------------
# ``?w=<px>`` on an image artifact serves a downscaled JPEG variant instead of
# the original, so conversation and artifact-panel previews load quickly while
# ``?download=true`` / the parameter-less URL still deliver the original bytes.
PREVIEW_VARIANT_MAX_WIDTH = 4096
PREVIEW_VARIANT_MIN_WIDTH = 16
PREVIEW_VARIANT_JPEG_QUALITY = 82
PREVIEW_VARIANT_FORMATS = {"jpg"}
_PREVIEW_VARIANT_RASTER_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
_PREVIEW_VARIANT_CACHE_DIR_NAME = ".image-derivatives"
_PREVIEW_VARIANT_CACHE_CONTROL = "private, max-age=31536000, immutable"


def _build_preview_variant_cache_path(actual_path: Path, width: int, fmt: str) -> Path:
    """Content-versioned cache path for a scaled variant.

    The key mixes the source file's ``mtime_ns``/size with the variant
    parameters, so an overwritten source gets a fresh cache entry while an
    unchanged file keeps hitting the same one (making the immutable
    ``Cache-Control`` header safe).
    """
    try:
        stat = actual_path.stat()
        version = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        version = "unknown"
    digest = hashlib.sha1(f"{version}:{width}:{fmt}".encode()).hexdigest()[:16]
    return actual_path.parent / _PREVIEW_VARIANT_CACHE_DIR_NAME / f"{actual_path.stem}.w{width}.{digest}.{fmt}"


def _render_preview_variant(source: Path, cache_path: Path, width: int, fmt: str) -> bytes:
    """Render a downscaled JPEG variant and store it in the cache; blocking IO."""
    from PIL import Image

    with Image.open(source) as img:
        img.load()
        # Long-edge fit: scale so the longer side == width, never upscale.
        long_edge = max(img.width, img.height)
        if long_edge > width:
            scale = width / long_edge
            new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        else:
            img = img.copy()
        # JPEG has no alpha: composite transparency onto white.
        if img.mode in ("RGBA", "LA", "P", "PA"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_name(f"{cache_path.name}.tmp-{id(img) & 0xFFFF:04x}")
    try:
        img.save(tmp_path, format="JPEG", quality=PREVIEW_VARIANT_JPEG_QUALITY, optimize=True)
        tmp_path.replace(cache_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return cache_path.read_bytes()


def _read_preview_variant(actual_path: Path, path: str, width: int, fmt: str) -> bytes:
    """Worker-thread body for the image-variant branch of ``get_artifact``.

    Raises ``HTTPException`` with the artifact router's existing status codes;
    returns the encoded JPEG bytes on success.
    """
    if not actual_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")
    if not actual_path.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")
    mime_type, _ = mimetypes.guess_type(actual_path)
    if mime_type not in _PREVIEW_VARIANT_RASTER_MIME_TYPES:
        raise HTTPException(status_code=415, detail=f"Scaled preview is only available for raster images: {path}")
    cache_path = _build_preview_variant_cache_path(actual_path, width, fmt)
    try:
        if cache_path.is_file():
            return cache_path.read_bytes()
        return _render_preview_variant(actual_path, cache_path, width, fmt)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to render image preview variant for %s (w=%d)", path, width)
        raise HTTPException(status_code=500, detail="Failed to render image preview variant")


def _build_content_disposition(disposition_type: str, filename: str) -> str:
    """Build an RFC 5987 encoded Content-Disposition header value."""
    return f"{disposition_type}; filename*=UTF-8''{quote(filename)}"


def _build_attachment_headers(filename: str, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"Content-Disposition": _build_content_disposition("attachment", filename)}
    if extra_headers:
        headers.update(extra_headers)
    return headers


def is_text_file_by_content(path: Path, sample_size: int = 8192) -> bool:
    """Check if file is text by examining content for null bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
            # Text files shouldn't contain null bytes
            return b"\x00" not in chunk
    except Exception:
        return False


def _read_skill_archive_member(zip_ref: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Read a .skill archive member while enforcing an uncompressed size cap."""
    if info.file_size > MAX_SKILL_ARCHIVE_MEMBER_BYTES:
        raise HTTPException(status_code=413, detail="Skill archive member is too large to preview")

    chunks: list[bytes] = []
    total_read = 0
    with zip_ref.open(info, "r") as src:
        while chunk := src.read(_SKILL_ARCHIVE_READ_CHUNK_SIZE):
            total_read += len(chunk)
            if total_read > MAX_SKILL_ARCHIVE_MEMBER_BYTES:
                raise HTTPException(status_code=413, detail="Skill archive member is too large to preview")
            chunks.append(chunk)
    return b"".join(chunks)


def _extract_file_from_skill_archive(zip_path: Path, internal_path: str) -> bytes | None:
    """Extract a file from a .skill ZIP archive.

    Args:
        zip_path: Path to the .skill file (ZIP archive).
        internal_path: Path to the file inside the archive (e.g., "SKILL.md").

    Returns:
        The file content as bytes, or None if not found.
    """
    if not zipfile.is_zipfile(zip_path):
        return None

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            # List all files in the archive
            infos_by_name = {info.filename: info for info in zip_ref.infolist()}

            # Try direct path first
            if internal_path in infos_by_name:
                return _read_skill_archive_member(zip_ref, infos_by_name[internal_path])

            # Try with any top-level directory prefix (e.g., "skill-name/SKILL.md")
            for name, info in infos_by_name.items():
                if name.endswith("/" + internal_path) or name == internal_path:
                    return _read_skill_archive_member(zip_ref, info)

            # Not found
            return None
    except (zipfile.BadZipFile, KeyError):
        return None


def _load_skill_archive_member(actual_skill_path: Path, skill_file_path: str, internal_path: str) -> tuple[bytes, str | None]:
    """Worker-thread body for the ``.skill`` branch of ``get_artifact``.

    The ``exists`` / ``is_file`` probes, the ZIP open+extract, and the MIME
    sniff (``mimetypes`` lazily stats the system MIME database on first use) are
    blocking filesystem IO and must stay off the event loop. Raised
    ``HTTPException``s propagate through ``asyncio.to_thread`` unchanged,
    preserving status codes.
    """
    if not actual_skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill file not found: {skill_file_path}")
    if not actual_skill_path.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {skill_file_path}")
    content = _extract_file_from_skill_archive(actual_skill_path, internal_path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File '{internal_path}' not found in skill archive")
    mime_type, _ = mimetypes.guess_type(internal_path)
    return content, mime_type


def _read_artifact_payload(actual_path: Path, path: str, download: bool) -> tuple[str, str | None, bytes | str | None]:
    """Worker-thread body for the regular branch of ``get_artifact``.

    Stat probes, MIME sniffing (``mimetypes`` lazily stats the system MIME
    database on first use), and full-file reads are all blocking filesystem IO.
    Returns a ``(kind, mime_type, payload)`` plan the handler turns into a
    response on the loop: ``("file", mime, None)`` (let ``FileResponse`` stream
    it), ``("text", mime, str)``, or ``("bytes", mime, bytes)``. Behavior/error
    codes match the previous inline logic.
    """
    if not actual_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")
    if not actual_path.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")
    mime_type, _ = mimetypes.guess_type(actual_path)
    # Active content / explicit download is streamed by FileResponse — no read here.
    if download or mime_type in ACTIVE_CONTENT_MIME_TYPES:
        return ("file", mime_type, None)
    if mime_type and mime_type.startswith("text/"):
        return ("text", mime_type, actual_path.read_text(encoding="utf-8"))
    if is_text_file_by_content(actual_path):
        return ("text", mime_type, actual_path.read_text(encoding="utf-8"))
    return ("bytes", mime_type, actual_path.read_bytes())


@router.get(
    "/threads/{thread_id}/artifacts/{path:path}",
    summary="Get Artifact File",
    description="Retrieve an artifact file generated by the AI agent. Text and binary files can be viewed inline, while active web content is always downloaded.",
)
@require_permission("threads", "read", owner_check=True)
async def get_artifact(
    thread_id: str,
    path: str,
    request: Request,
    download: bool = False,
    w: int | None = None,
    fmt: str | None = None,
) -> Response:
    """Get an artifact file by its path.

    The endpoint automatically detects file types and returns appropriate content types.
    Use the `download` query parameter to force file download for non-active content.

    Args:
        thread_id: The thread ID.
        path: The artifact path with virtual prefix (e.g., mnt/user-data/outputs/file.txt).
        request: FastAPI request object (automatically injected).

    Returns:
        The file content as a FileResponse with appropriate content type:
        - Active content (HTML/XHTML/SVG): Served as download attachment
        - Text files: Plain text with proper MIME type
        - Binary files: Inline display with download option

    Raises:
        HTTPException:
            - 400 if path is invalid or not a file
            - 403 if access denied (path traversal detected)
            - 404 if file not found

    Query Parameters:
        download (bool): If true, forces attachment download for file types that are
            otherwise returned inline or as plain text. Active HTML/XHTML/SVG content
            is always downloaded regardless of this flag.

    Example:
        - Get text file inline: `/api/threads/abc123/artifacts/mnt/user-data/outputs/notes.txt`
        - Download file: `/api/threads/abc123/artifacts/mnt/user-data/outputs/data.csv?download=true`
        - Active web content such as `.html`, `.xhtml`, and `.svg` artifacts is always downloaded
    """
    # Check if this is a request for a file inside a .skill archive (e.g., xxx.skill/SKILL.md)
    if ".skill/" in path:
        # Split the path at ".skill/" to get the ZIP file path and internal path
        skill_marker = ".skill/"
        marker_pos = path.find(skill_marker)
        skill_file_path = path[: marker_pos + len(".skill")]  # e.g., "mnt/user-data/outputs/my-skill.skill"
        internal_path = path[marker_pos + len(skill_marker) :]  # e.g., "SKILL.md"

        actual_skill_path = await asyncio.to_thread(resolve_thread_virtual_path, thread_id, skill_file_path)

        # Offload the stat probes + ZIP open/extract + MIME sniff (blocking filesystem IO).
        content, mime_type = await asyncio.to_thread(_load_skill_archive_member, actual_skill_path, skill_file_path, internal_path)

        # Add cache headers to avoid repeated ZIP extraction (cache for 5 minutes)
        cache_headers = {"Cache-Control": "private, max-age=300"}
        download_name = Path(internal_path).name or actual_skill_path.stem
        if download or mime_type in ACTIVE_CONTENT_MIME_TYPES:
            return Response(content=content, media_type=mime_type or "application/octet-stream", headers=_build_attachment_headers(download_name, cache_headers))

        if mime_type and mime_type.startswith("text/"):
            return PlainTextResponse(content=content.decode("utf-8"), media_type=mime_type, headers=cache_headers)

        # Default to plain text for unknown types that look like text
        try:
            return PlainTextResponse(content=content.decode("utf-8"), media_type="text/plain", headers=cache_headers)
        except UnicodeDecodeError:
            return Response(content=content, media_type=mime_type or "application/octet-stream", headers=cache_headers)

    actual_path = await asyncio.to_thread(resolve_thread_virtual_path, thread_id, path)

    logger.info(f"Resolving artifact path: thread_id={thread_id}, requested_path={path}, actual_path={actual_path}")

    # Scaled preview variant: `?w=720&fmt=jpg` on a raster image returns a
    # downscaled JPEG instead of the original bytes. The parameter-less URL
    # (and `?download=true`) keeps serving the untouched original.
    if w is not None and not download:
        variant_fmt = (fmt or "jpg").lower().lstrip(".")
        if variant_fmt not in PREVIEW_VARIANT_FORMATS:
            raise HTTPException(status_code=400, detail=f"Unsupported preview format: {fmt!r} (supported: {sorted(PREVIEW_VARIANT_FORMATS)})")
        if not PREVIEW_VARIANT_MIN_WIDTH <= w <= PREVIEW_VARIANT_MAX_WIDTH:
            raise HTTPException(status_code=400, detail=f"Preview width must be between {PREVIEW_VARIANT_MIN_WIDTH} and {PREVIEW_VARIANT_MAX_WIDTH}")
        variant_bytes = await asyncio.to_thread(_read_preview_variant, actual_path, path, w, variant_fmt)
        return Response(
            content=variant_bytes,
            media_type="image/jpeg",
            headers={
                "Cache-Control": _PREVIEW_VARIANT_CACHE_CONTROL,
                "Content-Disposition": _build_content_disposition("inline", actual_path.name),
            },
        )

    # Offload path stat + MIME sniff + file reads (all blocking filesystem IO).
    # Active content and explicit downloads are streamed by FileResponse, so the
    # worker only reports the kind; inline text/binary payloads are read in-thread.
    kind, mime_type, payload = await asyncio.to_thread(_read_artifact_payload, actual_path, path, download)

    if kind == "file":
        # Always force download for active content types to prevent script
        # execution in the application origin when users open generated artifacts.
        return FileResponse(path=actual_path, filename=actual_path.name, media_type=mime_type, headers=_build_attachment_headers(actual_path.name))

    if kind == "text":
        return PlainTextResponse(content=payload, media_type=mime_type)

    return Response(content=payload, media_type=mime_type, headers={"Content-Disposition": _build_content_disposition("inline", actual_path.name)})
