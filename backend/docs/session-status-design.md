# 改进会话状态检查与自动恢复

## Context

当前 `SessionHealthMonitor._check_all()` 是空操作（为防止误中断已禁用）。需要重新设计：
1. 精确的会话状态报告（主会话 + 子任务，区分等待工具/等待LLM/超时等）
2. 每 3 分钟自动检查，无进行中任务时激活主会话继续处理
3. 前端可查看详细会话状态

## 变更

### 1. 新增 Gateway API：获取会话状态概览

**文件**: `backend/app/gateway/routers/threads.py`

新增 `GET /api/threads/{thread_id}/status` 端点，返回：

```python
class SessionStatus(BaseModel):
    thread_id: str
    main_session: MainSessionStatus
    active_subtasks: list[SubtaskStatus]
    recent_subtasks: list[SubtaskStatus]  # 最近10个

class MainSessionStatus(BaseModel):
    status: str  # "running" | "idle" | "interrupted" | "error"
    run_id: str | None
    started_at: str | None
    last_updated: str | None
    last_message: str | None

class SubtaskStatus(BaseModel):
    task_id: str
    subagent_name: str
    description: str
    status: str  # "running" | "completed" | "failed" | "interrupted" | "timed_out"
    detail: str  # "waiting_for_tool" | "waiting_for_llm" | "idle" (仅 running 时有)
    started_at: str | None
    last_updated: str | None
    last_message: str | None
```

**数据来源**：
- 主会话状态：`client.runs.list(thread_id, limit=1)` → 取最新 run 的 status/created_at
- 子任务状态：合并 `_background_tasks`（内存）+ JSONL 文件（磁盘）
- 超时判定：`last_updated` 超过 10 分钟且 status=running → 标记 `timed_out`
- 等待工具 vs 等待 LLM：检查 JSONL 最后一条消息类型（tool → waiting_for_llm，ai → waiting_for_tool）

### 2. 重启 SessionHealthMonitor

**文件**: `backend/app/gateway/session_health_monitor.py`

- `check_interval` 改为 180（3 分钟）
- `_check_all()` 恢复为实际检查逻辑：
  1. 发现所有有子任务的线程（复用 `_discover_threads_with_sessions`）
  2. 对每个线程检查是否有活跃任务（子任务 running 或主会话 running）
  3. 无活跃任务 + 有未完成 todos → 调用 `_activate_thread` 发送恢复消息
- 新增 `_get_session_status(thread_id)` 方法：收集主会话+子任务状态，返回 `SessionStatus`
- 保留 `_activate_thread`（已存在，发送 recovery 消息）
- 保留 `_has_unfinished_todos`（已存在）
- 移除 `_check_orphan_sessions`（不再自动标记孤儿会话，改为只报告状态）

### 3. 前端：会话状态按钮

**文件**: 新增 `frontend/src/components/workspace/session-status-dialog.tsx`

- 在 chat 页面工具栏添加「会话状态」按钮
- 点击弹出 Dialog（Sheet），显示：
  - 主会话状态（运行中/空闲/中断）
  - 活跃子任务列表
  - 最近子任务列表（含状态、时间、最后消息）
- 调用 `GET /api/threads/{thread_id}/status` 获取数据
- 使用 TanStack Query 自动刷新（10s）

**文件**: `frontend/src/app/workspace/chats/[thread_id]/page.tsx`

- 在页面工具栏区域添加 SessionStatusButton

## 关键文件

| 文件 | 变更 |
|------|------|
| `backend/app/gateway/routers/threads.py` | 新增 GET /status 端点 |
| `backend/app/gateway/session_health_monitor.py` | 重启 _check_all，3 分钟检查 + 自动激活 |
| `backend/app/gateway/app.py` | 确认 monitor 启动参数 |
| `frontend/src/components/workspace/session-status-dialog.tsx` | 新增状态对话框 |
| `frontend/src/app/workspace/chats/[thread_id]/page.tsx` | 添加状态按钮 |
| `frontend/src/core/subagents/hooks.ts` | 新增 useSessionStatus hook |

## 验证

1. `GET /api/threads/{thread_id}/status` 返回正确状态
2. 主会话 running 时显示 running
3. 子任务运行中显示 waiting_for_tool / waiting_for_llm
4. 10 分钟无更新显示 timed_out
5. 所有子任务结束后，3 分钟内主会话自动激活
6. 前端按钮点击弹出状态面板
7. `make test` 通过
