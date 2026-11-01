# 会话健康监控修复 — 跨重启检测与卡死 Run 清理

## Context

SessionHealthMonitor 已实现基本的子任务假活检测和主会话激活，但存在以下盲区：

1. **重启后僵尸子任务不可见**：`_background_tasks` 是进程内存字典，重启后为空。磁盘上的 JSONL 文件可能存在无终止标记的 session（进程崩溃、超时等），但 monitor 无法检测。
2. **卡死的 LangGraph Run 阻塞一切**：run 卡在 "running" 状态数小时，导致所有 pending run（包括 recovery 消息）无法执行。
3. **主会话激活依赖 `_background_tasks`**：`_check_stalled_threads()` 只检查 `_background_tasks` 中的 thread_id，重启后为空。
4. **`_activate_thread()` 使用 `runs.wait()` 阻塞**：与 recovery.py 同样的问题，会导致 lifespan 阻塞。

## 修复内容

### 1. 子任务检测：增加磁盘扫描

**现有逻辑**（不变）：检查 `_background_tasks` 中 RUNNING 任务的 JSONL 是否过期

**新增逻辑**：扫描磁盘 JSONL 文件，找到无终止标记且不在 `_background_tasks` 中的 session

```
_check_orphan_sessions():
  遍历 threads/*/subagents/*.jsonl
  对每个 JSONL:
    - 有终止标记？→ 跳过
    - 在 _background_tasks 中？→ 跳过（由现有逻辑处理）
    - JSONL mtime < stale_threshold 前？→ 僵尸，标记为 interrupted
```

**动作**：标记为 interrupted（写终止标记到 JSONL），不自动 reactivation（因为缺少 original_prompt 等上下文）

### 2. 卡死 Run 检测与清理

**新增方法**：`_check_stuck_runs()`

```
对每个有 sub-agent session 的 thread:
  查询 LangGraph runs列表
  找到 status in (running, pending) 且 created_at 超过 stale_threshold 的 run
  → 取消这些 run
```

**时机**：在 `_check_stalled_threads()` 之前执行，确保 run 队列畅通

### 3. 主会话激活：增加磁盘扫描

**现有逻辑**（不变）：检查 `_background_tasks` 中的 thread

**新增逻辑**：扫描磁盘 thread 目录，找到有 sub-agent session 的 thread

```
_check_stalled_threads():
  thread_ids = _background_tasks 中的 thread_id  ∪  磁盘扫描到的 thread_id
  对每个 thread_id:
    所有 sub-agent session 都有终止标记？
    → 是：继续检查 todos 和用户中断状态
    → 有未终止的 session：跳过（由 _check_orphan_sessions 处理）
```

### 4. 非阻塞激活消息

`_activate_thread()` 改为 `asyncio.create_task()` + `client.runs.create()`（与 recovery.py 修复一致）

## 文件变更

| 文件 | 变更 |
|------|------|
| `app/gateway/session_health_monitor.py` | 修改：增加磁盘扫描、卡死 run 清理、非阻塞激活 |
| `tests/test_session_health_monitor.py` | 修改：增加新逻辑的测试 |

## 验证

1. 模拟重启场景：手动创建无终止标记的 JSONL，验证 monitor 检测并标记
2. 模拟卡死 run：创建长时间 pending 的 run，验证 monitor 取消
3. 验证主会话激活：所有 session 终止 + 有未完成 todos → 发送激活消息
4. 验证非阻塞：激活消息不阻塞 lifespan
