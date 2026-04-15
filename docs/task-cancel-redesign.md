# 任务停止机制重构 — 跨进程取消信号

## Context

用户反馈：无论通过会话状态对话框还是任务标题栏的停止按钮，都无法真正停止运行中的子任务。停止主会话也无效——尤其当主会话正在执行 tool call 时。

### 根因分析

DeerFlow 有两种运行模式：
- **标准模式** (`make dev`)：LangGraph Server (2024) 运行 agent，Gateway (8001) 只做 API 转发
- **Gateway 模式** (`make dev-pro`)：Gateway 内嵌 agent runtime

当前使用的是**标准模式**。这导致了一个关键的跨进程问题：

```
┌─────────────────────┐      ┌──────────────────────┐
│  LangGraph Server   │      │     Gateway (8001)    │
│     (port 2024)     │      │                      │
│                     │      │                      │
│  _background_tasks ◄───── 不共享内存 ────────► _background_tasks = {}  │
│  (有实际任务数据)    │      │  (空字典)             │
│                     │      │                      │
│  task_tool 执行      │      │  /api/runs/subtasks/ │
│  cancel_event       │      │  {id}/cancel 端点    │
└─────────────────────┘      └──────────────────────┘
         ▲                             ▲
         │                             │
    前端 stop 按钮              会话状态停止按钮
    → /api/langgraph/...       → /api/runs/subtasks/...
    → LangGraph 进程            → Gateway 进程 ❌
```

**问题 1：子任务取消无法跨进程**
- 子任务的 `_background_tasks` 和 `cancel_event` 在 LangGraph Server 进程内存中
- Gateway 的取消端点在 Gateway 进程中，`_background_tasks` 为空
- `request_cancel_background_task()` 设置的是 Gateway 进程的 `cancel_event`，不影响 LangGraph Server 进程
- 现有的磁盘 fallback（标记 summary.json 为 interrupted）只改状态，不停止执行

**问题 2：协作式取消太慢**
- `_aexecute()` 只在 `astream()` 每次迭代时检查 `cancel_event.is_set()`
- 单次迭代可能耗时很长（LLM 生成、工具调用如 bash 命令）
- 取消信号无法中断正在进行的 LLM 调用或工具执行

**问题 3：主会话停止依赖 LangGraph Server 的取消机制**
- `thread.stop()` 仅中止客户端 SSE 连接（AbortController.abort()）
- 后续显式调用 `POST /api/langgraph/threads/{id}/runs/{run_id}/cancel`
- LangGraph Server 的取消是协作式的——在图节点边界检查
- `task_tool` 是一个持续轮询的节点，轮询间隔 `await asyncio.sleep(5)`
- 如果 `CancelledError` 被注入，会在下一个 `await asyncio.sleep(5)` 时触发
- 但此信号无法传递给 Gateway 进程的子任务取消逻辑

### 解决方案：文件系统跨进程取消信号

Gateway 和 LangGraph Server 共享同一个文件系统。用一个 `.cancel` 标记文件作为跨进程取消信号。

```
Gateway 进程写 .cancel 文件 ──→ 共享文件系统 ←── LangGraph Server 进程读 .cancel 文件
```

## 设计

### 1. SubagentSession 增加取消信号方法

**文件**: `backend/packages/harness/deerflow/subagents/session.py`

```python
# 在 SubagentSession 类中新增：

def request_cancel(self) -> None:
    """Write a cancel signal file (cross-process safe)."""
    cancel_path = self.jsonl_path.parent / f"{self.task_id}.cancel"
    cancel_path.write_text("cancelled", encoding="utf-8")

def is_cancel_requested(self) -> bool:
    """Check if cancel was requested via signal file."""
    cancel_path = self.jsonl_path.parent / f"{self.task_id}.cancel"
    return cancel_path.exists()

def clear_cancel_signal(self) -> None:
    """Remove cancel signal file after processing."""
    cancel_path = self.jsonl_path.parent / f"{self.task_id}.cancel"
    try:
        cancel_path.unlink(missing_ok=True)
    except OSError:
        pass
```

### 2. _aexecute() 检查文件取消信号

**文件**: `backend/packages/harness/deerflow/subagents/executor.py`

在 `_aexecute()` 的 `async for chunk` 循环中，**同时**检查 `cancel_event` 和文件信号：

```python
# 现有检查 (line 267, 283)
if result.cancel_event.is_set():
    ...

# 新增文件信号检查（在 cancel_event 检查之后）
if self.session is not None and self.session.is_cancel_requested():
    logger.info("Subagent cancelled via file signal")
    with _background_tasks_lock:
        if result.status == SubagentStatus.RUNNING:
            result.status = SubagentStatus.CANCELLED
            result.error = "Cancelled by user"
            result.completed_at = datetime.now()
    self.session.mark_interrupted(message_count=_session_msg_count)
    self.session.clear_cancel_signal()
    return result
```

### 3. task_tool 轮询循环检查文件信号

**文件**: `backend/packages/harness/deerflow/tools/builtins/task_tool.py`

在轮询循环中（create 和 resume 的 `while True` 循环），每次迭代检查文件信号：

```python
# 在 while True 循环内，await asyncio.sleep(5) 之前
if session is not None and session.is_cancel_requested():
    request_cancel_background_task(task_id)
    session.clear_cancel_signal()
    writer({"type": "task_cancelled", "task_id": task_id, "error": "Cancelled by user"})
    cleanup_background_task(task_id)
    return "Task cancelled by user."
```

这使得主会话的 `task_tool` 能在 5 秒内检测到文件取消信号并退出。

### 4. request_cancel_background_task() 同时写文件

**文件**: `backend/packages/harness/deerflow/subagents/executor.py`

修改 `request_cancel_background_task()` 使其同时设置内存事件和文件信号：

```python
def request_cancel_background_task(task_id: str) -> None:
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is not None:
            result.cancel_event.set()
    # Cross-process: always try writing cancel file
    _write_cancel_file(task_id)
    logger.info("Requested cancellation for background task %s", task_id)

def _write_cancel_file(task_id: str) -> None:
    """Write a cancel signal file for cross-process cancellation."""
    try:
        from deerflow.config.paths import get_paths
        threads_dir = get_paths().base_dir / "threads"
        if not threads_dir.exists():
            return
        for thread_dir in threads_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            summary_path = thread_dir / "subagents" / f"{task_id}.summary.json"
            if summary_path.exists():
                cancel_path = thread_dir / "subagents" / f"{task_id}.cancel"
                cancel_path.write_text("cancelled", encoding="utf-8")
                return
    except Exception:
        logger.debug("Failed to write cancel file for task %s", task_id, exc_info=True)
```

### 5. Gateway 取消端点简化

**文件**: `backend/app/gateway/routers/runs.py`

简化 `cancel_subtask` 端点，直接写文件信号：

```python
@router.post("/subtasks/{task_id}/cancel")
async def cancel_subtask(task_id: str, request: Request):
    from deerflow.subagents.executor import get_background_task_result, request_cancel_background_task

    # Try in-memory cancel (same process, gateway mode)
    result = get_background_task_result(task_id)
    if result is not None and result.status.value in ("running", "pending"):
        request_cancel_background_task(task_id)
        return CancelSubtaskResponse(task_id=task_id, cancelled=True)

    # Cross-process: write cancel file (standard mode, task runs in LangGraph Server)
    request_cancel_background_task(task_id)  # This also writes .cancel file now
    return CancelSubtaskResponse(task_id=task_id, cancelled=True)
```

## 关键文件

| 文件 | 变更 |
|------|------|
| `backend/packages/harness/deerflow/subagents/session.py` | 新增 `request_cancel()`, `is_cancel_requested()`, `clear_cancel_signal()` |
| `backend/packages/harness/deerflow/subagents/executor.py` | `_aexecute()` 增加文件信号检查；`request_cancel_background_task()` 同时写文件 |
| `backend/packages/harness/deerflow/tools/builtins/task_tool.py` | 轮询循环（create + resume）增加文件信号检查 |
| `backend/app/gateway/routers/runs.py` | 简化取消端点，直接调用 `request_cancel_background_task()` |
| `backend/tests/test_subagent_session.py` | 新增取消信号测试 |
| `backend/tests/test_session_health_monitor.py` | 验证相关测试仍通过 |

## 取消路径汇总

| 场景 | 路径 | 延迟 |
|------|------|------|
| **子任务停止按钮**（会话状态对话框） | Gateway 写 `.cancel` → `task_tool` 轮询检测 → 5s 内退出 | ≤ 5s |
| **子任务停止按钮**（任务卡片） | Gateway 写 `.cancel` → `task_tool` 轮询检测 → 5s 内退出 | ≤ 5s |
| **主会话停止** | SDK abort SSE + LangGraph cancel → `CancelledError` in polling → `request_cancel_background_task()` → 同时设 `cancel_event` 和写 `.cancel` | ≤ 5s |
| **子任务执行内部** | `.cancel` 文件检查 + `cancel_event` 检查，每个 `astream()` 迭代 | 下次迭代 |
| **Zombie 任务**（重启后） | 磁盘 summary 标记 interrupted（现有逻辑） | 即时 |

## 验证

1. **子任务取消**：启动一个运行中的子任务，通过会话状态对话框点击停止 → 任务在 5 秒内停止
2. **主会话取消**：主会话正在执行 task_tool，点击停止 → 主会话和子任务都停止
3. **跨进程**：标准模式下，Gateway 的取消端点能通过文件信号停止 LangGraph Server 中的任务
4. **幂等性**：多次点击停止按钮不报错
5. **清理**：取消后 `.cancel` 文件被清理
6. **现有测试**：`make test` 全部通过
