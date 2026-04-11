"""单元测试：主会话文件持久化 — 写入、读取、去重。

验证 MainSessionMiddleware：
1. 写入消息到 conversation.jsonl，字段完整，不截断
2. 同一会话内按 ID 去重（滑动窗口，最近 10 条）
3. 通过 thread_id 读取文件会话数据，验证结构正确
4. 路径格式 {base_dir}/threads/{thread_id}/conversation.jsonl
"""

import json
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

_MOCKED_MODULES = [
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.subagents.executor",
]


@pytest.fixture(autouse=True, scope="module")
def _mock_heavy_deps():
    saved = {name: sys.modules.get(name) for name in _MOCKED_MODULES}
    for name in _MOCKED_MODULES:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()
    yield
    for name in _MOCKED_MODULES:
        if saved[name] is None and name in sys.modules:
            del sys.modules[name]
        elif saved[name] is not None:
            sys.modules[name] = saved[name]


@pytest.fixture(autouse=True)
def _reset_imports():
    for mod in list(sys.modules.keys()):
        if "deerflow.agents.middlewares.main_session_middleware" in mod:
            del sys.modules[mod]
    yield


@pytest.fixture
def tmp_thread_dir(tmp_path):
    d = tmp_path / "threads" / "test-thread"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def mock_paths(tmp_path, tmp_thread_dir):
    from deerflow.config import paths as paths_mod

    mock_p = MagicMock()
    mock_p.thread_dir.return_value = tmp_thread_dir

    with patch.object(paths_mod, "get_paths", return_value=mock_p):
        yield mock_p


def _make_middleware(dedup_window=10):
    from deerflow.agents.middlewares.main_session_middleware import MainSessionMiddleware
    return MainSessionMiddleware(dedup_window=dedup_window)


def _jsonl_path(tmp_thread_dir):
    return tmp_thread_dir / "conversation.jsonl"


def _write_realistic_jsonl(path, count=5):
    """写入模拟的真实会话 JSONL 文件。"""
    entries = []
    for i in range(count):
        if i % 3 == 0:
            entries.append({"ts": "2026-04-11T01:00:00+00:00", "role": "human", "id": f"h-{i:04d}", "content": f"问题 {i}"})
        elif i % 3 == 1:
            e = {"ts": "2026-04-11T01:00:01+00:00", "role": "ai", "id": f"a-{i:04d}", "content": f"回答 {i}"}
            if i % 6 == 1:
                e["tool_calls"] = [{"id": f"tc-{i}", "name": "bash", "args": {"cmd": f"cmd-{i}"}}]
            entries.append(e)
        else:
            entries.append({"ts": "2026-04-11T01:00:02+00:00", "role": "tool", "id": f"t-{i:04d}", "content": f"结果 {i}", "tool_call_id": f"tc-{i-1}", "name": "bash"})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")
    return entries


# ── 写入验证 ────────────────────────────────────────────────────────────────


class TestWrite:
    """验证写入的 JSONL 文件内容正确。"""

    def test_write_all_message_types(self, mock_paths, tmp_thread_dir):
        """写入 Human/AI/Tool 三种消息，验证字段完整。"""
        mw = _make_middleware()
        msgs = [
            HumanMessage(content="你好", id="h-001"),
            AIMessage(
                content="我来帮你",
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {"cmd": "ls"}}],
                id="a-001",
            ),
            ToolMessage(content="file1.txt\nfile2.txt", tool_call_id="tc-1", name="bash", id="t-001"),
        ]
        new = mw._get_new_messages("test-thread", msgs)
        mw._write_messages(_jsonl_path(tmp_thread_dir), new)

        lines = _jsonl_path(tmp_thread_dir).read_text().strip().split("\n")
        assert len(lines) == 3

        e0 = json.loads(lines[0])
        assert e0["role"] == "human"
        assert e0["content"] == "你好"
        assert e0["id"] == "h-001"
        assert "ts" in e0

        e1 = json.loads(lines[1])
        assert e1["role"] == "ai"
        assert e1["id"] == "a-001"
        assert e1["tool_calls"][0]["name"] == "bash"

        e2 = json.loads(lines[2])
        assert e2["role"] == "tool"
        assert e2["id"] == "t-001"
        assert e2["tool_call_id"] == "tc-1"
        assert e2["name"] == "bash"

    def test_write_creates_file(self, mock_paths, tmp_thread_dir):
        """首次写入时自动创建文件。"""
        mw = _make_middleware()
        assert not _jsonl_path(tmp_thread_dir).exists()
        mw._write_messages(_jsonl_path(tmp_thread_dir), [HumanMessage(content="first", id="h1")])
        assert _jsonl_path(tmp_thread_dir).exists()

    def test_write_appends(self, mock_paths, tmp_thread_dir):
        """多次写入是追加模式，不会覆盖。"""
        mw = _make_middleware()
        mw._write_messages(_jsonl_path(tmp_thread_dir), [HumanMessage(content="A", id="h1")])
        mw._write_messages(_jsonl_path(tmp_thread_dir), [AIMessage(content="B", id="a1")])

        lines = _jsonl_path(tmp_thread_dir).read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["content"] == "A"
        assert json.loads(lines[1])["content"] == "B"

    def test_no_truncation(self, mock_paths, tmp_thread_dir):
        """大内容不截断，如实记录。"""
        mw = _make_middleware()
        big = "x" * 100_000
        mw._write_messages(
            _jsonl_path(tmp_thread_dir),
            [ToolMessage(content=big, tool_call_id="tc1", name="bash", id="t1")],
        )
        entry = json.loads(_jsonl_path(tmp_thread_dir).read_text().strip())
        assert entry["content"] == big
        assert "TRUNCATED" not in entry["content"]


# ── 去重验证 ────────────────────────────────────────────────────────────────


class TestDedup:
    """同一会话内按消息 ID 去重（滑动窗口）。"""

    def test_dedup_same_session(self, mock_paths, tmp_thread_dir):
        """相同消息不重复写入。"""
        mw = _make_middleware()
        msgs = [HumanMessage(content="Hi", id="h1"), AIMessage(content="Hello", id="a1")]

        new1 = mw._get_new_messages("test-thread", msgs)
        assert len(new1) == 2

        new2 = mw._get_new_messages("test-thread", msgs)
        assert len(new2) == 0

    def test_incremental_append(self, mock_paths, tmp_thread_dir):
        """多轮对话，每轮只追加新消息。"""
        mw = _make_middleware()

        turn1 = [HumanMessage(content="Q1", id="h1"), AIMessage(content="A1", id="a1")]
        mw._get_new_messages("test-thread", turn1)
        mw._write_messages(_jsonl_path(tmp_thread_dir), turn1)

        turn2 = turn1 + [ToolMessage(content="r", tool_call_id="tc1", name="bash", id="t1"), AIMessage(content="A2", id="a2")]
        new2 = mw._get_new_messages("test-thread", turn2)
        assert len(new2) == 2
        mw._write_messages(_jsonl_path(tmp_thread_dir), new2)

        lines = _jsonl_path(tmp_thread_dir).read_text().strip().split("\n")
        assert len(lines) == 4
        roles = [json.loads(l)["role"] for l in lines]
        assert roles == ["human", "ai", "tool", "ai"]

    def test_summarization_dedup(self, mock_paths, tmp_thread_dir):
        """摘要替换消息列表后，已有 ID 的消息被跳过。"""
        mw = _make_middleware()
        mw._get_new_messages("test-thread", [
            HumanMessage(content="Q1", id="h1"),
            AIMessage(content="A1", id="a1"),
            HumanMessage(content="Q2", id="h2"),
            AIMessage(content="A2", id="a2"),
        ])

        summarized = [
            HumanMessage(content="Summary", id="sum1"),
            AIMessage(content="A2", id="a2"),
            AIMessage(content="A3", id="a3"),
        ]
        new = mw._get_new_messages("test-thread", summarized)
        assert [m.id for m in new] == ["sum1", "a3"]

    def test_messages_without_id_always_new(self, mock_paths, tmp_thread_dir):
        """无 ID 的消息每次都被视为新消息。"""
        mw = _make_middleware()
        new1 = mw._get_new_messages("test-thread", [HumanMessage(content="no-id")])
        assert len(new1) == 1
        new2 = mw._get_new_messages("test-thread", [HumanMessage(content="no-id")])
        assert len(new2) == 1  # 无 ID，无法去重

    def test_sliding_window_eviction(self, mock_paths, tmp_thread_dir):
        """超过窗口大小的旧 ID 被淘汰，可能重复写入。"""
        mw = _make_middleware(dedup_window=3)
        # Write 3 messages (fills window)
        mw._get_new_messages("test-thread", [
            HumanMessage(content="M0", id="h0"),
            AIMessage(content="M1", id="a1"),
            HumanMessage(content="M2", id="h2"),
        ])
        # Write 3 more — h0 evicted from window
        mw._get_new_messages("test-thread", [
            AIMessage(content="M3", id="a3"),
            HumanMessage(content="M4", id="h4"),
            AIMessage(content="M5", id="a5"),
        ])
        # h0 is no longer in window → treated as new
        new = mw._get_new_messages("test-thread", [HumanMessage(content="M0", id="h0")])
        assert len(new) == 1  # h0 re-admitted (window evicted it)


# ── 读取验证 ────────────────────────────────────────────────────────────────


class TestReadByThreadId:
    """通过 thread_id 读取 conversation.jsonl，验证数据正确。"""

    def test_read_all_messages(self, mock_paths, tmp_thread_dir):
        """通过 thread_id 定位文件，读取全部消息，验证结构完整。"""
        _write_realistic_jsonl(_jsonl_path(tmp_thread_dir), count=9)

        mw = _make_middleware()
        jsonl = mw._get_jsonl_path("test-thread")
        assert jsonl.exists()

        messages = []
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                messages.append(json.loads(line.strip()))

        assert len(messages) == 9
        for i, m in enumerate(messages):
            assert "ts" in m, f"Line {i}: missing ts"
            assert "role" in m, f"Line {i}: missing role"
            assert "content" in m, f"Line {i}: missing content"
            assert "id" in m, f"Line {i}: missing id"

        roles = [m["role"] for m in messages]
        assert roles == ["human", "ai", "tool"] * 3

    def test_read_then_write_new(self, mock_paths, tmp_thread_dir):
        """读取已有文件后，通过中间件追加新消息，文件追加正确。"""
        _write_realistic_jsonl(_jsonl_path(tmp_thread_dir), count=3)

        mw = _make_middleware()
        # 旁路写入不读文件，所有消息都视为新
        restored = [
            HumanMessage(content="问题 0", id="h-0000"),
            AIMessage(content="回答 1", id="a-0001"),
            ToolMessage(content="结果 2", tool_call_id="tc-1", name="bash", id="t-0002"),
            HumanMessage(content="新问题", id="h-new"),
        ]
        new = mw._get_new_messages("test-thread", restored)
        assert len(new) == 4  # 全部写入（旁路，不读文件去重）
        mw._write_messages(_jsonl_path(tmp_thread_dir), new)

        lines = _jsonl_path(tmp_thread_dir).read_text().strip().split("\n")
        assert len(lines) == 7  # 3 original + 4 new

    def test_read_different_threads(self, mock_paths, tmp_path):
        """不同 thread_id 读取各自独立的文件。"""
        mw = _make_middleware()
        for tid in ["thread-a", "thread-b"]:
            td = tmp_path / "threads" / tid
            td.mkdir(parents=True, exist_ok=True)
            _write_realistic_jsonl(td / "conversation.jsonl", count=3)

        mock_paths.thread_dir.return_value = tmp_path / "threads" / "thread-a"
        jsonl_a = mw._get_jsonl_path("thread-a")
        msgs_a = [json.loads(l.strip()) for l in open(jsonl_a) if l.strip()]

        mock_paths.thread_dir.return_value = tmp_path / "threads" / "thread-b"
        jsonl_b = mw._get_jsonl_path("thread-b")
        msgs_b = [json.loads(l.strip()) for l in open(jsonl_b) if l.strip()]

        assert len(msgs_a) == 3
        assert len(msgs_b) == 3
        assert jsonl_a != jsonl_b


# ── 路径格式验证 ────────────────────────────────────────────────────────────


class TestPathFormat:
    def test_path_structure(self, mock_paths, tmp_thread_dir):
        mw = _make_middleware()
        path = mw._get_jsonl_path("test-thread")
        assert path.name == "conversation.jsonl"
        assert path.parent.name == "test-thread"
        assert path.parent.parent.name == "threads"


# ── 并发验证 ────────────────────────────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_threads(self, mock_paths, tmp_path):
        mw = _make_middleware()
        errors = []

        def write_turn(idx):
            try:
                tid = f"thread-{idx}"
                td = tmp_path / "threads" / tid
                td.mkdir(parents=True, exist_ok=True)
                for j in range(5):
                    msgs = [HumanMessage(content=f"M{j}", id=f"t{idx}-m{j}")]
                    new = mw._get_new_messages(tid, msgs)
                    if new:
                        mw._write_messages(td / "conversation.jsonl", new)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_turn, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        for i in range(5):
            lines = (tmp_path / "threads" / f"thread-{i}" / "conversation.jsonl").read_text().strip().split("\n")
            assert len(lines) == 5
