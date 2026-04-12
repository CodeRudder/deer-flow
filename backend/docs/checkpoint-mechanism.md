# LangGraph Checkpoint 机制分析

## 概述

LangGraph 使用 checkpoint 机制持久化线程状态。每个节点（agent node、tool node）执行完成后，系统自动创建一个 checkpoint 保存当前完整状态。

## 1. Checkpoint 生命周期

### 创建时机

每个 LangGraph 节点执行完成后自动创建 checkpoint：

```
Agent Node → checkpoint A → Tool Node → checkpoint B → Agent Node → checkpoint C
```

checkpoint 包含：
- **channel_values**: 所有状态字段（messages, todos, artifacts, title 等）
- **metadata**: created_at, step, source
- **parent_config**: 指向父 checkpoint（形成链表）
- **tasks**: 待执行/中断的任务
- **pending_writes**: 未提交的写入

### Checkpoint 链

```
checkpoint_C (最新)
    ↑ parent
checkpoint_B
    ↑ parent
checkpoint_A (最早)
```

前端加载时始终获取**最新 checkpoint** 的状态。

### Checkpointer 配置

`backend/langgraph.json`:
```json
{
  "checkpointer": {
    "path": "./packages/harness/deerflow/agents/checkpointer/async_provider.py:make_checkpointer"
  }
}
```

支持的后端：
- **memory**: `InMemorySaver`（默认，重启丢失）
- **sqlite**: 持久化到 `.db` 文件
- **postgres**: 分布式持久化

## 2. 前端状态加载

### useStream Hook

`frontend/src/core/threads/hooks.ts`:

```typescript
const thread = useStream<AgentThreadState>({
  client: getAPIClient(isMock),
  assistantId: "lead_agent",
  threadId: onStreamThreadId,
  reconnectOnMount: runMetadataStorageRef.current
    ? () => runMetadataStorageRef.current!
    : false,
  fetchStateHistory: { limit: 50 },
});
```

### 加载流程

```
页面打开 → useStream 初始化
  ├── 有 threadId?
  │   ├── Yes → client.threads.getState(threadId) → 加载最新 checkpoint
  │   └── No → 等待用户输入创建新线程
  ├── reconnectOnMount?
  │   ├── Yes (sessionStorage 中有 run_id) → 尝试重连该 run 的 SSE
  │   └── No → 仅加载状态，不重连
  └── fetchStateHistory → 获取最近 50 个 checkpoint 历史
```

### 关键参数

| 参数 | 作用 | 默认值 |
|------|------|--------|
| `reconnectOnMount` | 刷新页面后重连到上次中断的 run | false |
| `fetchStateHistory` | 加载 checkpoint 历史数量 | { limit: 50 } |

### sessionStorage 机制

- Key: `lg:stream:${thread_id}`
- Value: run_id
- 用途: 页面刷新后恢复流式连接

## 3. 中断与恢复

### 中断行为

当 run 被取消/中断时：

1. **interrupt 模式**（默认）：保留当前 checkpoint，状态不变
2. **rollback 模式**（未实现）：回退到 pre-run checkpoint

### 中断后的状态

```
用户发送消息 → run 开始 → checkpoint X
  → agent 执行 → checkpoint Y
  → 用户中断 → checkpoint Z（中断点）
```

前端刷新后加载 **checkpoint Z**（最新），包含中断前的所有消息。

### update_state 创建新 Checkpoint

`client.threads.update_state()` 会**创建新的 checkpoint**：

```
checkpoint Z (中断点)
    ↑ parent
checkpoint Z+1 (update_state 添加的消息)
```

**注意**: `update_state` 不执行 graph，只修改状态并保存为新 checkpoint。

## 4. 已知问题

### 4.1 自动恢复导致 Checkpoint 链增长

**问题**: `session_health_monitor` 和 `recovery.py` 通过 `runs.stream`/`runs.create`/`update_state` 创建了大量 checkpoint。

**影响**: 
- 前端加载时可能获取到中间 checkpoint
- Recovery 消息触发 agent 执行，产生大量新消息和 checkpoint

**解决方案**: 已禁用自动恢复（`_check_stalled_threads` 和 `auto_recover_interrupted_tasks`）。

### 4.2 Rollback 未实现

**问题**: 取消 run 后无法回退到执行前的 checkpoint。

**位置**: `backend/packages/harness/deerflow/runtime/runs/worker.py` Phase 2 TODO。

**影响**: 取消后前端看到的是中断点的状态，不是执行前的状态。

### 4.3 多次 Recovery 创建冗余 Checkpoint

**问题**: 每次 `update_state` 调用创建新 checkpoint，多次恢复导致链增长。

**影响**: 性能下降，加载时间增加。

### 4.4 todos 状态持久化

**机制**: `write_todos` tool call 执行后，tool node 完成 → 自动创建 checkpoint → todos 新值保存。

**潜在问题**: 如果流式传输中断，最后一个 `write_todos` 的 checkpoint 可能未被保存。

## 5. 建议

1. **实现 rollback**: 取消 run 时回退到 pre-run checkpoint
2. **Checkpoint 清理**: 定期合并/删除中间 checkpoint
3. **禁用自动恢复**: 避免系统自动创建 run 修改线程状态
4. **增加历史限制**: 使 `fetchStateHistory` limit 可配置
5. **添加 checkpoint 验证**: 确保 state 一致性
