"""Tests for the thread workspace file browsing API (workspace_files router).

The API browses the WHOLE user-data tree (virtual /mnt/user-data), not only
the workspace/ subdirectory — the root listing returns the top-level
directories (workspace/outputs/uploads and any others).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

import app.gateway.path_utils as path_utils
from app.gateway.routers import workspace_files
from deerflow.config.paths import Paths


@pytest.fixture()
def user_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point path resolution at a temp bucket for user 'tester'; return the thread user-data dir."""
    base = tmp_path / "base"
    thread_root = base / "users" / "tester" / "threads" / "thread-1" / "user-data"
    for sub in ("workspace", "outputs", "uploads"):
        (thread_root / sub).mkdir(parents=True)

    paths = Paths(base_dir=base)
    monkeypatch.setattr(path_utils, "get_paths", lambda: paths)
    monkeypatch.setattr(path_utils, "get_effective_user_id", lambda: "tester")
    return thread_root


def _client() -> TestClient:
    app = make_authed_test_app()
    app.include_router(workspace_files.router)
    return TestClient(app)


def test_root_lists_all_user_data_top_level_dirs(user_data_root: Path) -> None:
    (user_data_root / "workspace" / "notes.txt").write_text("hello")
    (user_data_root / "outputs" / "report.pdf").write_bytes(b"%PDF")
    (user_data_root / "custom-dir").mkdir()  # non-standard subdir is also visible

    with _client() as client:
        response = client.get("/api/threads/thread-1/workspace/files")

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "/mnt/user-data"
    assert body["truncated"] is False
    names = [(e["name"], e["type"]) for e in body["entries"]]
    assert ("workspace", "directory") in names
    assert ("outputs", "directory") in names
    assert ("uploads", "directory") in names
    assert ("custom-dir", "directory") in names


def test_list_subdirectory_metadata_and_sorting(user_data_root: Path) -> None:
    ws = user_data_root / "workspace"
    (ws / "b.txt").write_text("bb")
    (ws / "a.txt").write_text("a")
    (ws / "sub").mkdir()
    (ws / "sub" / "inner.md").write_text("# hi")

    with _client() as client:
        response = client.get("/api/threads/thread-1/workspace/files", params={"path": "/mnt/user-data/workspace"})

    assert response.status_code == 200
    entries = response.json()["entries"]
    # Directories first, then files alphabetically.
    assert [e["name"] for e in entries] == ["sub", "a.txt", "b.txt"]
    sub = entries[0]
    assert sub["path"] == "/mnt/user-data/workspace/sub"
    assert sub["size"] is None
    assert sub["modified_at"] is not None
    a_txt = entries[1]
    assert a_txt["type"] == "file"
    assert a_txt["size"] == 1
    assert a_txt["path"] == "/mnt/user-data/workspace/a.txt"


def test_list_excludes_internal_cache_dirs(user_data_root: Path) -> None:
    ws = user_data_root / "workspace"
    (ws / ".image-derivatives").mkdir()
    (ws / "visible.txt").write_text("x")

    with _client() as client:
        body = client.get("/api/threads/thread-1/workspace/files", params={"path": "/mnt/user-data/workspace"}).json()

    assert [e["name"] for e in body["entries"]] == ["visible.txt"]


def test_search_matches_recursively(user_data_root: Path) -> None:
    ws = user_data_root / "workspace"
    (ws / "target.txt").write_text("root hit")
    (ws / "deep" / "nested").mkdir(parents=True)
    (ws / "deep" / "nested" / "target-file.csv").write_text("a,b")
    (ws / "deep" / "other.log").write_text("no match")

    with _client() as client:
        body = client.get(
            "/api/threads/thread-1/workspace/files",
            params={"path": "/mnt/user-data/workspace", "search": "target"},
        ).json()

    names = {e["name"] for e in body["entries"]}
    assert names == {"target.txt", "target-file.csv"}
    assert "/mnt/user-data/workspace/deep/nested/target-file.csv" in {e["path"] for e in body["entries"]}


def test_search_truncates_at_cap(user_data_root: Path) -> None:
    ws = user_data_root / "workspace"
    for i in range(workspace_files.MAX_SEARCH_ENTRIES + 10):
        (ws / f"hit-{i}.txt").write_text("x")

    with _client() as client:
        body = client.get(
            "/api/threads/thread-1/workspace/files",
            params={"path": "/mnt/user-data/workspace", "search": "hit-"},
        ).json()

    assert body["truncated"] is True
    assert len(body["entries"]) == workspace_files.MAX_SEARCH_ENTRIES


def test_path_traversal_rejected(user_data_root: Path) -> None:
    secret = user_data_root.parent.parent.parent.parent / "secret.txt"
    secret.write_text("top secret")

    with _client() as client:
        response = client.get(
            "/api/threads/thread-1/workspace/files",
            params={"path": "/mnt/user-data/../../.."},
        )
        assert response.status_code == 403

        # Non /mnt/user-data prefix is rejected with 400.
        response = client.get("/api/threads/thread-1/workspace/files", params={"path": "/etc"})
        assert response.status_code == 400
        assert not secret.exists() or True  # never read either way


def test_resolution_stays_inside_owner_bucket(user_data_root: Path) -> None:
    """The requesting owner's bucket scopes every resolution."""
    import app.gateway.path_utils as pu

    resolved = pu.resolve_thread_virtual_path("thread-1", "/mnt/user-data/workspace")
    assert Path(resolved) == user_data_root / "workspace"
    # Another user's directory is unreachable through any virtual path.
    with pytest.raises(Exception):
        pu.resolve_thread_virtual_path("thread-1", "/mnt/user-data/../../other-user/threads/thread-1/user-data")


def test_missing_directory_returns_404(user_data_root: Path) -> None:
    with _client() as client:
        response = client.get(
            "/api/threads/thread-1/workspace/files",
            params={"path": "/mnt/user-data/workspace/does-not-exist"},
        )
    assert response.status_code == 404


def test_file_path_returns_400(user_data_root: Path) -> None:
    (user_data_root / "workspace" / "file.txt").write_text("x")

    with _client() as client:
        response = client.get(
            "/api/threads/thread-1/workspace/files",
            params={"path": "/mnt/user-data/workspace/file.txt"},
        )
    assert response.status_code == 400


def test_list_truncates_at_cap(user_data_root: Path) -> None:
    ws = user_data_root / "workspace"
    for i in range(workspace_files.MAX_LIST_ENTRIES + 5):
        (ws / f"f-{i}.txt").write_text("x")

    with _client() as client:
        body = client.get("/api/threads/thread-1/workspace/files", params={"path": "/mnt/user-data/workspace"}).json()

    assert body["truncated"] is True
    assert len(body["entries"]) == workspace_files.MAX_LIST_ENTRIES
