import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.gateway.routers import threads
from deerflow.config.paths import Paths


def test_delete_thread_data_removes_thread_directory(tmp_path):
    paths = Paths(tmp_path)
    thread_dir = paths.thread_dir("thread-cleanup")
    workspace = paths.sandbox_work_dir("thread-cleanup")
    uploads = paths.sandbox_uploads_dir("thread-cleanup")
    outputs = paths.sandbox_outputs_dir("thread-cleanup")

    for directory in [workspace, uploads, outputs]:
        directory.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    (uploads / "report.pdf").write_bytes(b"pdf")
    (outputs / "result.json").write_text("{}", encoding="utf-8")

    assert thread_dir.exists()

    response = threads._delete_thread_data("thread-cleanup", paths=paths)

    assert response.success is True
    assert not thread_dir.exists()


def test_delete_thread_data_is_idempotent_for_missing_directory(tmp_path):
    paths = Paths(tmp_path)

    response = threads._delete_thread_data("missing-thread", paths=paths)

    assert response.success is True
    assert not paths.thread_dir("missing-thread").exists()


def test_delete_thread_data_rejects_invalid_thread_id(tmp_path):
    paths = Paths(tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        threads._delete_thread_data("../escape", paths=paths)

    assert exc_info.value.status_code == 422
    assert "Invalid thread_id" in exc_info.value.detail


def test_delete_thread_route_cleans_thread_directory(tmp_path):
    paths = Paths(tmp_path)
    thread_dir = paths.thread_dir("thread-route")
    paths.sandbox_work_dir("thread-route").mkdir(parents=True, exist_ok=True)
    (paths.sandbox_work_dir("thread-route") / "notes.txt").write_text("hello", encoding="utf-8")

    app = FastAPI()
    app.include_router(threads.router)

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        with TestClient(app) as client:
            response = client.delete("/api/threads/thread-route")

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "Deleted local thread data for thread-route"}
    assert not thread_dir.exists()


def test_delete_thread_route_rejects_invalid_thread_id(tmp_path):
    paths = Paths(tmp_path)

    app = FastAPI()
    app.include_router(threads.router)

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        with TestClient(app) as client:
            response = client.delete("/api/threads/../escape")

    assert response.status_code == 404


def test_delete_thread_route_returns_422_for_route_safe_invalid_id(tmp_path):
    paths = Paths(tmp_path)

    app = FastAPI()
    app.include_router(threads.router)

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        with TestClient(app) as client:
            response = client.delete("/api/threads/thread.with.dot")

    assert response.status_code == 422
    assert "Invalid thread_id" in response.json()["detail"]


def test_delete_thread_data_returns_generic_500_error(tmp_path):
    paths = Paths(tmp_path)

    with (
        patch.object(paths, "delete_thread_dir", side_effect=OSError("/secret/path")),
        patch.object(threads.logger, "exception") as log_exception,
    ):
        with pytest.raises(HTTPException) as exc_info:
            threads._delete_thread_data("thread-cleanup", paths=paths)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to delete local thread data."
    assert "/secret/path" not in exc_info.value.detail
    log_exception.assert_called_once_with("Failed to delete thread data for %s", "thread-cleanup")


def test_prepare_history_response_injects_message_timestamps(tmp_path):
    paths = Paths(tmp_path)
    thread_id = "dafbc7b6-2023-4725-a62d-a1c968e58c2d"
    thread_dir = paths.thread_dir(thread_id)
    thread_dir.mkdir(parents=True, exist_ok=True)
    (thread_dir / "conversation.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-05-11T06:38:08+00:00",
                        "role": "human",
                        "id": "msg-old",
                        "content": "old question",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-05-29T04:00:00+00:00",
                        "role": "human",
                        "id": "msg-new",
                        "content": "new question",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    history = [
        {
            "metadata": {"created_at": "2026-05-29T04:00:00+00:00"},
            "values": {
                "messages": [
                    {
                        "type": "human",
                        "id": "msg-old",
                        "content": "old question",
                        "response_metadata": {},
                    },
                    {
                        "type": "human",
                        "id": "msg-new",
                        "content": "new question",
                        "response_metadata": {},
                    },
                ]
            },
        }
    ]

    with patch("deerflow.config.paths.get_paths", return_value=paths):
        result = threads._prepare_history_response(thread_id, history)

    messages = result[0]["values"]["messages"]
    assert messages[0]["response_metadata"]["created_at"] == "2026-05-11T06:38:08+00:00"
    assert messages[1]["response_metadata"]["created_at"] == "2026-05-29T04:00:00+00:00"


def test_dedupe_messages_by_id_keeps_first_occurrence():
    messages = [
        {"id": "msg-1", "ts": "2026-05-11T06:29:28+00:00", "content": "original"},
        {"id": "msg-2", "ts": "2026-05-11T06:30:00+00:00", "content": "other"},
        {"id": "msg-1", "ts": "2026-05-29T04:00:00+00:00", "content": "replayed"},
        {"content": "no id"},
    ]

    result = threads._dedupe_messages_by_id(messages)

    assert result == [
        {"id": "msg-1", "ts": "2026-05-11T06:29:28+00:00", "content": "original"},
        {"id": "msg-2", "ts": "2026-05-11T06:30:00+00:00", "content": "other"},
        {"content": "no id"},
    ]
