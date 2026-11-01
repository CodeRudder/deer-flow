# 健康监控器激活失败修复方案

## Context

健康监控器（SessionHealthMonitor）负责定期扫描 stalled 的会话线程并发送激活消息。
排查发现以下问题导致线程无法被正确激活。

## 问题清单

### 问题 1：LLM 错误标记永久阻止激活

**线程**: `cce2df85` (17/22 todos 未完成)

**现象**: 最后一条 AI 消息是 LLM 错误 `"LLM 服务暂时不可用"`，带 `additional_kwargs.llm_error=True`。
`_is_recent_llm_error()` 返回 `True`，健康监控器永久跳过此线程。

**根因**: `_is_recent_llm_error()` 只检查消息是否存在 `llm_error` 标记，没有时间窗口限制。
LLM 恢复后线程永远无法被重新激活。

**文件**: `backend/app/gateway/session_health_monitor.py:440-466`

**修复**: 检查消息时间戳，仅在 LLM 错误发生后的冷却期（默认 10 分钟）内阻止激活。
如果消息没有时间戳，检查消息在列表中的位置（最近 N 条之内视为 recent）。

```python
async def _is_recent_llm_error(self, thread_id: str) -> bool:
    """Check if the last AI message is a RECENT LLM error (within cooldown)."""
    client = self._get_client()
    if client is None:
        return False
    try:
        state = await client.threads.get_state(thread_id)
        messages = state.get("values", {}).get("messages", [])
        for msg in reversed(messages):
            if msg.get("type") == "ai":
                extra = msg.get("additional_kwargs", {})
                if extra.get("llm_error"):
                    # Check cooldown: only block if error is recent
                    msg_ts = msg.get("additional_kwargs", {}).get("error_ts")
                    if msg_ts:
                        from datetime import datetime, timezone, timedelta
                        try:
                            error_time = datetime.fromisoformat(msg_ts)
                            if error_time.tzinfo is None:
                                error_time = error_time.replace(tzinfo=timezone.utc)
                            age = datetime.now(tz=timezone.utc) - error_time
                            return age < timedelta(minutes=self._llm_error_cooldown_minutes)
                        except (ValueError, TypeError):
                            pass
                    # No timestamp — check by position (last 5 messages)
                    idx = len(messages) - 1 - messages[::-1].index(msg)
                    return idx >= len(messages) - 5
                return False
        return False
    except Exception:
        return False
```

同时需要在 `LLMErrorHandlingMiddleware` 的错误消息中加入 `error_ts` 时间戳字段，
供健康监控器判断错误的新旧程度。

**文件**: `backend/packages/harness/deerflow/agents/middlewares/llm_error_handling_middleware.py`

在所有错误 AIMessage 的 `additional_kwargs` 中加入当前时间戳：
```python
additional_kwargs={"llm_error": True, "error_reason": reason, "error_ts": _utc_now_iso()}
```

### 问题 2：Zombie 线程浪费检查周期

**线程**: `38ada324` （磁盘有数据，LangGraph Server 已删除）

**现象**: 健康监控器每 3 分钟通过磁盘扫描发现此线程，调用 LangGraph API 检查时全部返回 404，
生成大量 WARNING 日志，浪费检查时间。

**根因**: `_discover_threads_with_sessions()` 通过磁盘扫描发现线程，但不验证线程是否仍存在于 LangGraph。
已删除的线程只在磁盘上残留目录。

**文件**: `backend/app/gateway/session_health_monitor.py:294-339`

**修复**: 在 `_check_and_activate_thread` 开头增加线程存在性检查，404 时直接跳过。

```python
async def _check_and_activate_thread(self, thread_id: str) -> None:
    # Skip threads that don't exist in LangGraph
    if not await self._thread_exists(thread_id):
        return
    # ... existing checks ...

async def _thread_exists(self, thread_id: str) -> bool:
    """Check if thread exists in LangGraph. Cache result for 1 hour."""
    client = self._get_client()
    if client is None:
        return False
    try:
        state = await client.threads.get_state(thread_id)
        return state is not None
    except Exception:
        return False
```

### 问题 3：检查周期超时过短

**现象**: `_check_cycle` 的 `future.result(timeout=30)` 频繁超时（TimeoutError），
导致整个检查周期失败，已发现的线程无法完成激活。

**根因**: 有多个线程需要检查，每个线程需要多次 HTTP 请求（get_state, runs.list 等），
30 秒不够。

**文件**: `backend/app/gateway/session_health_monitor.py:100`

**修复**: 将超时从 30 秒增加到 60 秒。

```python
future.result(timeout=60)
```

## 关键文件

| 文件 | 变更 |
|------|------|
| `backend/app/gateway/session_health_monitor.py` | LLM 错误冷却期、线程存在性检查、超时增加 |
| `backend/packages/harness/deerflow/agents/middlewares/llm_error_handling_middleware.py` | 错误消息加 `error_ts` 时间戳 |
| `backend/tests/test_session_health_monitor.py` | 新增/更新测试 |

## 验证

1. LLM 错误 10 分钟后，健康监控器应能重新激活线程
2. 已删除线程（404）不再产生 WARNING 日志
3. 检查周期超时减少
4. 现有测试通过
