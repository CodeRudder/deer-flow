# 自动迭代 — 3级任务编排

## 概述

DeerFlow 支持三级自动任务编排，无需人工干预即可持续推进复杂目标：

```
自动迭代 (auto_iteration)
  └─ 会话激活 (session_monitor)
       └─ 子任务执行 (subagents)
```

| 级别 | 触发条件 | 动作 |
|------|---------|------|
| **子任务执行** | Agent 调用 `task` 工具 | 后台并行执行子任务 |
| **会话激活** | 会话空闲 + 有未完成 todos | 发送激活提示词，恢复执行 |
| **自动迭代** | 会话空闲 + 所有 todos 已完成 | 发送迭代提示词，启动下一轮 |

---

## 配置

### 1. 会话激活（session_monitor）

检测卡住的会话（有未完成 todos 但无活跃 run），自动发送激活消息恢复执行。

```yaml
session_monitor:
  enabled: true
  check_interval: 180          # 检查间隔（秒），默认 180
  stale_threshold: 300         # 子任务无响应超时（秒），默认 300
  langgraph_url: http://langgraph:2024   # Docker 环境使用容器名
  activation_message: "请按要求使用子任务继续处理未完成任务计划。"  # 全局默认

  # 可选：针对特定会话设置不同的激活提示词
  sessions:
    - thread_id: "your-thread-id"
      activation_message: "针对该会话的激活提示词"
```

### 2. 自动迭代（auto_iteration）

当配置的会话所有 todos 都完成后，自动发送迭代提示词启动下一轮。

```yaml
auto_iteration:
  enabled: false
  sessions:
    - thread_id: "your-thread-id"
      enabled: true
      max_iterations: 10           # 每轮最多迭代次数（默认 10）
      max_duration_seconds: 3600   # 每轮最长持续时间（秒，默认 3600）
      iteration_prompt: |
        所有任务已完成。请根据目标规划并启动下一轮迭代任务。
```

**达到限制后**：重置计数器，本轮结束，不发送任何消息。下次检查时重新开始新一轮。

---

## 检查逻辑（单次检查周期）

每个配置的 `auto_iteration` 会话，按以下顺序检查：

```
1. 有运行中的子任务？→ 跳过（等待子任务完成）
2. 有活跃的主 run？→ 跳过（等待 run 完成）
3. 用户手动中断？→ 跳过（不干预用户操作）
4. 有未完成 todos？→ 会话激活（发送 activation_message）
5. 有 todos 且全部完成？→ 自动迭代（发送 iteration_prompt）
6. 没有 todos？→ 跳过（尚未开始计划）
```

---

## 典型使用场景

### 场景：持续优化代码库

```yaml
session_monitor:
  enabled: true
  check_interval: 300
  langgraph_url: http://langgraph:2024
  activation_message: "请继续执行未完成的代码优化任务。"

auto_iteration:
  enabled: true
  sessions:
    - thread_id: "code-review-thread-id"
      max_iterations: 20
      max_duration_seconds: 86400   # 24小时
      iteration_prompt: |
        本轮代码优化已完成。请分析代码库中下一个需要改进的模块，
        制定优化计划并开始执行。
```

**执行流程**：
1. 用户在会话中启动计划模式，Agent 制定 todos 并开始执行
2. Agent 调用 `task` 工具派发子任务并行处理
3. 子任务完成后，Agent 更新 todos 状态
4. 所有 todos 完成 → `auto_iteration` 触发，发送迭代提示词
5. Agent 制定新一轮 todos，循环继续
6. 达到 `max_iterations` → 本轮结束，等待下一轮重置

---

## 注意事项

- `langgraph_url` 在 Docker 环境中需使用容器名（`http://langgraph:2024`），本地开发用 `http://localhost:2024`
- `check_interval` 修改后需重启 gateway 生效（monitor 在启动时读取配置）
- `auto_iteration` 的迭代状态保存在内存中，gateway 重启后计数器重置
- 建议在 `is_plan_mode: true` 的会话中使用，确保 Agent 维护 todos 列表
- 子任务执行需在 `context.subagent_enabled: true` 时才可用
