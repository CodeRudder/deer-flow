# 会话健康监控与激活恢复 — 后端定时任务

## Context

多 Agent 协作场景中，主会话和子任务可能因为进程崩溃、网络故障、模型超时等原因中断。当所有子任务都停止且主会话空闲时，整个会话停滞无法推进。

当前状态：
- `SubagentHealthMonitor`（`packages/harness/deerflow/subagents/health_monitor.py`）已在子任务进程内做假活检测和 reactivation，但它运行在子任务执行线程中，只在 Lead Agent 进程内存活时有效
- `recovery.py` 只在 Gateway 启动时一次性扫描中断的子任务
- 没有运行时的主会话激活机制

**目标**：Gateway 后端定时任务，监控会话健康并自动激活恢复：
1. 子任务假活检测与激活
2. 主会话激活（所有子任务中断 + 非用户中断 + 任务未完成）

---

## 激活条件

### 子任务假活检测与激活

**条件**：子任务 `_background_tasks` 中状态为 `RUNNING`，但对应 JSONL 日志文件超过 `stale_threshold` 秒未更新

**动作**：向子任务发送恢复消息，让其继续执行

> 复用 `SubagentHealthMonitor._reactivate_task()` 的逻辑：标记中断 → 构建 recovery prompt → 创建新 executor 提交

### 主会话激活

**条件**（全部满足才激活）：
1. 所有子任务状态都不是 `running`/`pending`（都已完成、失败或中断）
2. 主会话最后一次 run 非用户主动中断
3. 会话中存在未完成的 todos（有 `in_progress` 或 `pending` 状态的项）

**动作**：通过 `langgraph_sdk` 向主会话发送激活消息，让其检查进度继续推进

**用户中断判定**：检查最近一次 run 的 metadata，如果有 `"cancelled_by": "user"` 标记则跳过

---

## 架构设计

### SessionHealthMonitor 类

位置：`app/gateway/session_health_monitor.py`

```
SessionHealthMonitor
  ├── __init__(check_interval, stale_threshold, langgraph_url)
  ├── start(loop) -> None            # 启动定时任务
  ├── stop() -> None                 # 停止定时任务
  ├── _schedule_next()               # threading.Timer 递归调度
  ├── _check_cycle()                 # try/except + reschedule
  ├── _check_all() -> async          # 主入口
  │
  ├── 子任务检测
  │   ├── _check_subagent_tasks() -> async
  │   │   遍历 _background_tasks，找 RUNNING 且 JSONL 过期的
  │   │   调用 SubagentHealthMonitor._reactivate_task()
  │   │
  ├── 主会话激活
  │   ├── _check_stalled_threads() -> async
  │   │   条件检查：子任务全部停止 + 非用户中断 + 有未完成 todos
  │   │   调用 _activate_thread()
  │   │
  │   ├── _activate_thread(thread_id) -> async
  │   │   langgraph_sdk: 发送激活消息到主会话
  │   │
  │   └── _has_unfinished_todos(thread_id) -> async
  │       查询 thread state 检查 todos 状态
  │
  └── 辅助
      ├── _get_langgraph_client()     # 懒初始化 SDK client
      └── _is_user_interrupted(thread_id) -> async  # 检查最近 run 是否用户中断
```

### 定时任务模式

- 使用 `threading.Timer`（与 `SubagentHealthMonitor` 一致）
- `_check_cycle` 在 timer 线程中执行，通过 `asyncio.run_coroutine_threadsafe()` 桥接异步调用到 Gateway 事件循环
- 异常不中断调度，总是 reschedule

### 集成到 Gateway lifespan

在 `app/gateway/app.py` 的 `lifespan()` 中：
- `langgraph_runtime()` 初始化之后启动
- `yield` 之后停止
- 位置：在 `auto_recover_interrupted_tasks()` 之后

```python
# Start session health monitor
session_monitor = SessionHealthMonitor(
    check_interval=..., stale_threshold=..., langgraph_url=...
)
session_monitor.start(asyncio.get_event_loop())
app.state.session_monitor = session_monitor

yield

# Stop session health monitor
getattr(app.state, "session_monitor", None)?.stop()
```

---

## 配置

在 `config.yaml` 中添加：

```yaml
session_health_monitor:
  enabled: true              # 是否启用（默认 true）
  check_interval: 120        # 检查间隔秒数（默认 120）
  stale_threshold: 300       # 子任务假活阈值秒数（默认 300 = 5 分钟）
  langgraph_url: null        # LangGraph Server URL（默认 http://localhost:2024）
```

通过 `get_app_config().model_extra.get("session_health_monitor", {})` 读取。

---

## 激活消息格式

### 子任务恢复消息

复用 `SubagentHealthMonitor._reactivate_task()` 构建的 recovery prompt：

```
<recovery>
任务因 session stale for Xs 被中断。已执行 N 步。
最后完成的工作：...
原始任务：...
请继续完成剩余工作，不要重复已完成的步骤。
</recovery>

原始 prompt...
```

### 主会话激活消息

```
<session_recovery>
所有子任务已停止，但以下任务尚未完成：
- [in_progress] 任务A
- [pending] 任务B

请检查每个子任务的完成情况，继续推进未完成的工作。
</session_recovery>
```

---

## 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/gateway/session_health_monitor.py` | **新建** | SessionHealthMonitor 类 |
| `app/gateway/app.py` | **修改** | lifespan 集成 |
| `tests/test_session_health_monitor.py` | **新建** | 单元测试 |
| `docs/SESSION_HEALTH_MONITOR.md` | **新建** | 本文档 |

### 依赖的现有模块

| 模块 | 用途 |
|------|------|
| `SubagentHealthMonitor`（`packages/harness/deerflow/subagents/health_monitor.py`） | 复用 `_reactivate_task()` 逻辑 |
| `SubagentResult`/`_background_tasks`（`packages/harness/deerflow/subagents/executor.py`） | 子任务状态查询 |
| `SubagentSession`（`packages/harness/deerflow/subagents/session.py`） | session 查询 |
| `langgraph_sdk` | 主会话消息发送和状态查询 |
| `RunManager`（`packages/harness/deerflow/runtime/runs/manager.py`） | Gateway 模式 run 追踪 |

---

## 测试策略

1. **子任务假活检测**：mock `_background_tasks` 中的 RUNNING 任务 + 过期 JSONL → 验证 reactivation 被调用
2. **主会话激活条件**：
   - 全部条件满足 → 验证激活消息发送
   - 有子任务仍在运行 → 不激活
   - 用户中断 → 不激活
   - 无未完成 todos → 不激活
3. **定时调度**：start/stop/异常不中断调度
4. **LangGraph Server 不可达**：优雅降级，不中断调度

---

## 实现顺序

1. `session_health_monitor.py` 核心类
2. `app.py` lifespan 集成
3. `test_session_health_monitor.py` 单元测试
4. 集成验证
