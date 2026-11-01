import json

from scripts.sanitize_session_jsonl_base64 import (
    iter_session_jsonl_files,
    sanitize_jsonl_file,
    sanitize_session_jsonl_tree,
)


def _write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")


def test_sanitize_jsonl_file_rewrites_base64_and_preserves_invalid_lines(tmp_path):
    jsonl = tmp_path / "threads" / "thread-1" / "conversation.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "role": "human",
                "content": [
                    {"type": "text", "text": "viewed"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,SECRETBASE64"}},
                ],
            },
            "not json\n",
            {"role": "ai", "content": "ok"},
        ],
    )

    result = sanitize_jsonl_file(jsonl)

    raw = jsonl.read_text(encoding="utf-8")
    lines = raw.splitlines()
    first = json.loads(lines[0])
    assert result.changed
    assert result.total_lines == 3
    assert result.changed_lines == 1
    assert result.invalid_lines == 1
    assert "SECRETBASE64" not in raw
    assert lines[1] == "not json"
    assert first["content"][1] == {"type": "text", "text": "[图片 base64 已省略：image/png]"}


def test_sanitize_jsonl_file_dry_run_does_not_write(tmp_path):
    jsonl = tmp_path / "users" / "user-1" / "threads" / "thread-1" / "subagents" / "task-1.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "role": "human",
                "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "SECRETBASE64"}}],
            }
        ],
    )
    before = jsonl.read_text(encoding="utf-8")

    result = sanitize_jsonl_file(jsonl, dry_run=True)

    assert result.changed
    assert result.changed_lines == 1
    assert jsonl.read_text(encoding="utf-8") == before


def test_sanitize_jsonl_file_writes_backup(tmp_path):
    jsonl = tmp_path / "threads" / "thread-1" / "subagents" / "task-1.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "role": "tool",
                "content": [{"type": "image", "base64": "SECRETBASE64", "mime_type": "image/png"}],
            }
        ],
    )
    before = jsonl.read_text(encoding="utf-8")

    result = sanitize_jsonl_file(jsonl, backup=True)

    assert result.changed
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert result.backup_path.read_text(encoding="utf-8") == before
    assert "SECRETBASE64" not in jsonl.read_text(encoding="utf-8")


def test_iter_session_jsonl_files_only_targets_session_logs(tmp_path):
    expected = [
        tmp_path / "threads" / "thread-1" / "conversation.jsonl",
        tmp_path / "threads" / "thread-1" / "subagents" / "task-1.jsonl",
        tmp_path / "users" / "user-1" / "threads" / "thread-2" / "conversation.jsonl",
        tmp_path / "users" / "user-1" / "threads" / "thread-2" / "subagents" / "task-2.jsonl",
    ]
    ignored = [
        tmp_path / "threads" / "thread-1" / "other.jsonl",
        tmp_path / "users" / "user-1" / "threads" / "thread-2" / "subagents" / "task-2.summary.json",
        tmp_path / "misc" / "conversation.jsonl",
    ]
    for path in expected + ignored:
        _write_jsonl(path, [{"role": "human", "content": "ok"}])

    found = set(iter_session_jsonl_files(tmp_path))

    assert found == set(expected)


def test_sanitize_session_jsonl_tree_summarizes_results(tmp_path):
    _write_jsonl(
        tmp_path / "threads" / "thread-1" / "conversation.jsonl",
        [{"role": "human", "content": "data:image/png;base64,SECRETBASE64"}],
    )
    _write_jsonl(
        tmp_path / "threads" / "thread-1" / "subagents" / "task-1.jsonl",
        [{"role": "ai", "content": "ok"}],
    )

    summary = sanitize_session_jsonl_tree(tmp_path)

    assert summary.files_scanned == 2
    assert summary.files_changed == 1
    assert summary.lines_scanned == 2
    assert summary.lines_changed == 1
