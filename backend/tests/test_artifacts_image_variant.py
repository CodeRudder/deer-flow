"""Tests for the scaled image-preview variant of the artifacts router.

``GET /api/threads/{id}/artifacts/{path}?w=720&fmt=jpg`` serves a downscaled
JPEG variant of raster image artifacts; the parameter-less URL and
``?download=true`` keep serving the untouched original.
"""

import asyncio
import io
from pathlib import Path

import pytest
from _router_auth_helpers import call_unwrapped
from PIL import Image

import app.gateway.routers.artifacts as artifacts_router


def _make_request() -> object:
    from starlette.requests import Request

    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})


def _png_bytes(tmp_path: Path, size: tuple[int, int], color=(255, 0, 0, 128), name: str = "photo.png") -> Path:
    img = Image.new("RGBA", size, color)
    path = tmp_path / name
    img.save(path, format="PNG")
    return path


def test_variant_returns_downscaled_jpeg(tmp_path, monkeypatch) -> None:
    source = _png_bytes(tmp_path, (2400, 1200))
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _t, _p: source)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/photo.png", _make_request(), w=720, fmt="jpg"))

    assert response.media_type == "image/jpeg"
    assert "immutable" in response.headers.get("cache-control", "")
    img = Image.open(io.BytesIO(bytes(response.body)))
    long_edge = max(img.size)
    assert long_edge == 720
    assert len(response.body) < source.stat().st_size


def test_variant_never_upscales_small_image(tmp_path, monkeypatch) -> None:
    source = _png_bytes(tmp_path, (400, 200))
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _t, _p: source)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/photo.png", _make_request(), w=720, fmt="jpg"))

    img = Image.open(io.BytesIO(bytes(response.body)))
    assert img.size == (400, 200)


def test_variant_png_transparency_gets_white_background(tmp_path, monkeypatch) -> None:
    source = _png_bytes(tmp_path, (1200, 600), color=(0, 0, 255, 0))
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _t, _p: source)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/photo.png", _make_request(), w=720, fmt="jpg"))

    img = Image.open(io.BytesIO(bytes(response.body)))
    assert img.mode == "RGB"
    # Fully transparent pixel composites onto white.
    assert img.getpixel((0, 0)) == (255, 255, 255)


def test_variant_is_cached_on_disk(tmp_path, monkeypatch) -> None:
    source = _png_bytes(tmp_path, (1200, 600))
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _t, _p: source)

    first = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/photo.png", _make_request(), w=720, fmt="jpg"))
    cache_files = list((tmp_path / ".image-derivatives").glob("*.jpg"))
    assert len(cache_files) == 1

    # Second hit must come from the cache: the render step is poisoned so a
    # re-render would blow up loudly instead of silently recomputing.
    def _no_render(*_args, **_kwargs):
        raise AssertionError("variant must be served from cache on the second request")

    monkeypatch.setattr(artifacts_router, "_render_preview_variant", _no_render)
    second = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/photo.png", _make_request(), w=720, fmt="jpg"))
    assert bytes(second.body) == bytes(first.body)


def test_variant_cache_key_changes_when_source_changes(tmp_path, monkeypatch) -> None:
    source = _png_bytes(tmp_path, (1200, 600))
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _t, _p: source)

    asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/photo.png", _make_request(), w=720, fmt="jpg"))
    # Overwrite the source with different content.
    Image.new("RGBA", (800, 800), (0, 255, 0, 255)).save(source, format="PNG")
    asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/photo.png", _make_request(), w=720, fmt="jpg"))

    assert len(list((tmp_path / ".image-derivatives").glob("*.jpg"))) == 2


def test_original_request_unchanged_without_params(tmp_path, monkeypatch) -> None:
    source = _png_bytes(tmp_path, (1200, 600))
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _t, _p: source)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/photo.png", _make_request()))

    assert bytes(response.body) == source.read_bytes()
    assert response.headers.get("content-type", "").startswith("image/png")


def test_variant_rejects_bad_width_and_format(tmp_path, monkeypatch) -> None:
    from fastapi import HTTPException

    source = _png_bytes(tmp_path, (1200, 600))
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _t, _p: source)

    for params in ({"w": 1, "fmt": "jpg"}, {"w": 100000, "fmt": "jpg"}, {"w": 720, "fmt": "webp"}):
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/photo.png", _make_request(), **params))
        assert excinfo.value.status_code == 400


def test_variant_rejects_non_image(tmp_path, monkeypatch) -> None:
    from fastapi import HTTPException

    source = tmp_path / "doc.txt"
    source.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _t, _p: source)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/doc.txt", _make_request(), w=720, fmt="jpg"))
    assert excinfo.value.status_code == 415


def test_variant_missing_file_404(tmp_path, monkeypatch) -> None:
    from fastapi import HTTPException

    missing = tmp_path / "gone.png"
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _t, _p: missing)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/gone.png", _make_request(), w=720, fmt="jpg"))
    assert excinfo.value.status_code == 404


def test_path_traversal_still_rejected(tmp_path, monkeypatch) -> None:
    from fastapi import HTTPException

    def _raise_traversal(_tid: str, _p: str) -> Path:
        # Mirrors app.gateway.path_utils.resolve_thread_virtual_path's
        # ValueError → 403 conversion for traversal errors.
        raise HTTPException(status_code=403, detail="path traversal detected: ../../etc/passwd")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", _raise_traversal)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/../../etc/passwd", _make_request(), w=720, fmt="jpg"))
    assert excinfo.value.status_code == 403
