# 跨进程子任务取消机制设计

## 背景

DeerFlow 标准模式下，Gateway（FastAPI，端口 8001）和 LangGraph Server（端口 2024）是两个独立进程。
子任务（subtask）通过 `task_tool` 在 LangGraph 进程的 `_background_tasks` 字典和线程池中执行。
Gateway 的 cancel API 只能操作自己进程内的 `_background_tasks`，无法触达 LangGraph 进程中的执行线程。

## 方案：Gateway 通过 LangGraph SDK 取消 run → 触发 task_tool CancelledError

### 核心思路

取消子任务的本质是停止正在执行该子任务的 Lead Agent run。
当 LangGraph run 被取消时，会触发 `asyncio.CancelledError`，
`task_tool` 已有处理：捕获 CancelledError → 调用 `request_cancel_background_task(task_id)` → 设置 cancel_event →
executor 在下一个 astream 迭代退出。

这是已有的同进程取消链路，无需额外发明机制。

### 信号流

```
Gateway                                    LangGraph
────────                                   ─────────
POST /api/runs/subtasks/{id}/cancel
  1. 找到 task 对应的 thread_id + run_id
  2. 调用 LangGraph SDK:
     client.runs.cancel(thread_id, run_id)
                                           → run 被取消
                                           → task_tool polling 收到 CancelledError
                                           → request_cancel_background_task(task_id)
                                           → executor cancel_event.set()
                                           → 下一个 astream 迭代退出
                                           → session.mark_cancelled()
  3. 更新 Gateway 本地状态（磁盘 session summary）
  4. 返回 cancel 结果
```

### 关键问题：如何找到 run_id？

cancel API 只接收 `task_id`（即 `tool_call_id`），但需要 `run_id` 来取消 LangGraph run。

**方案**：Gateway 已有线程状态查询能力，可以：
1. 从 task_id 查找 thread_id（扫描 session 文件，已有逻辑）
2. 列出 thread 的 runs，找到 status=running 的 run_id
3. 调用 `client.runs.cancel(thread_id, run_id)`

### 改动范围

| 文件 | 改动 |
|------|------|
| `runs.py` cancel_subtask | Gateway 取消路径：查找 run_id → 调用 LangGraph SDK cancel run |
| `session.py` | 移除文件标记相关代码（`request_cancel`/`is_cancel_requested`/`clear_cancel_marker`） |
| `executor.py` | 移除文件标记检查，恢复纯 cancel_event 检查 |
| `task_tool.py` | 移除 polling 中的文件标记检查 |

### 不改动的部分

- `task_tool.py` 的 `asyncio.CancelledError` 处理 — 已有，无需改
- `executor.py` 的 `cancel_event` 检查 — 已有，无需改
- `session.py` 的 `mark_cancelled()` — 已有，保留

### 边界情况

1. **Gateway 模式（同进程）**：`_background_tasks` 在同一进程，直接 `request_cancel_background_task` 即可，无需调 LangGraph SDK
2. **无活跃 run**：子任务可能已结束或服务重启后找不到 run，降级为磁盘标记
3. **多个 running run**：理论上只有一个 Lead Agent run 在执行，取 status=running 的第一个
4. **取消失败**：SDK 调用异常时降级为磁盘标记方案

### 降级策略

```
Gateway cancel API:
  if task in _background_tasks (同进程):
    → request_cancel_background_task()  # 直接取消
  elif can reach LangGraph API:
    → find run_id → client.runs.cancel()  # 跨进程取消
  else:
    → 磁盘标记降级（保留文件标记作为 fallback）
```
