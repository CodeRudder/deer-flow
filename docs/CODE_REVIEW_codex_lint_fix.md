# Codex 修改 Code Review

**日期**：2026-05-10
**分支**：`feat/background-commands`
**范围**：Codex 修复 `make lint` 和 `make test` 的所有未提交改动（69 files, +787/-783 lines）
**假设**：所有未暂存改动均由 Codex 完成，目的仅为通过 lint/test

---

## 1. Findings

### Critical

#### C1. `recovery.py`：自动恢复被重新启用（此前已被明确禁用）

**文件**：`backend/app/gateway/recovery.py:86-132`

`_notify_thread` 从 `client.threads.update_state()` 改为 `client.runs.create()`，且使用 `multitask_strategy="interrupt"`。更关键的是，`auto_recover_interrupted_tasks()` 原来是一个**空操作**——只打日志、不发送任何东西。原始代码有一段详细注释解释禁用原因：

> Creating runs blocks the main session / update_state fails when in-flight runs exist / Recovery messages trigger agent to execute code directly

Codex 删除了该注释并恢复了完整的自动恢复行为。这可能导致：

- 恢复 run 会 interrupt 正在进行的用户会话（使用了 interrupt 策略）
- Gateway 重启时会突发大量并发 LangGraph API 调用
- 触发 agent 执行代码（原始注释中提到的问题）

**风险**：重新引入了当初导致该功能被禁用的同一批问题。Gateway 重启后会突然为所有被中断的 session 创建 recovery run。

**建议**：确认团队是否确实要启用自动恢复。如启用，需考虑增加速率限制和批量处理。

---

#### C2. `input-box.tsx`：`||` 改为 `??` — submit 按钮可能在 streaming 时仍可点击

**文件**：`frontend/src/components/workspace/input-box.tsx:843`

```diff
-              disabled={disabled || status === "streaming"}
+              disabled={disabled ?? status === "streaming"}
```

`??`（nullish coalescing）只在 `disabled` 为 `null` 或 `undefined` 时生效。如果 `disabled` 被显式设为 `false`：

- `false || status === "streaming"` → `status === "streaming"` ✅ 正确
- `false ?? status === "streaming"` → `false` ❌ **按钮在 streaming 期间不会被禁用**

如果父组件传了 `disabled={false}`，submit 按钮将无法在 streaming 时禁用，可能导致重复提交。

**建议**：改回 `||`。这个改动对 lint 来说没有必要，且引入了运行时语义 bug。

---

### High

#### H1. `session_monitor.py`：`multitask_strategy` 从 `reject` 改为 `interrupt`

**文件**：`backend/app/gateway/session_monitor.py:529`

```diff
-            "multitask_strategy": "reject",
+            "multitask_strategy": "interrupt",
```

此前，如果 thread 有活跃 run，session activation 会 **reject** 激活请求。现在它会 **interrupt** 活跃 run。这意味着：

- 用户正在进行的对话可能被 health monitor 的激活消息打断
- docstring 也被更新以反映这一变更，说明是有意为之

**风险**：活跃的用户会话可能被 monitor 非自愿地 interrupt。`reject` 策略是一层安全保护。

**建议**：确认这个改动是否是有意的。如果确实需要 interrupt 行为，确保用户能感知到。

---

#### H2. `subagents_config.py`：默认超时从 20 分钟降到 15 分钟

**文件**：`backend/packages/harness/deerflow/config/subagents_config.py:29`

```diff
-        default=1200,
+        default=900,
```

所有没有显式配置超时的 subagent 现在会在 15 分钟而非 20 分钟后超时。如果存在合法运行 15-20 分钟的长任务，这些任务现在会失败。

**建议**：验证现有任务是否从未运行超过 15 分钟。如果有，这是一个静默 regression。

---

#### H3. `general_purpose.py`：`max_turns` 从 300 降到 100

**文件**：`backend/packages/harness/deerflow/subagents/builtins/general_purpose.py:57`

```diff
-    max_turns=300,
+    max_turns=100,
```

通用 subagent 的最大迭代次数从 300 降到 100。复杂任务可能过早触发此限制。

**建议**：确认 100 是否足够。这是一个大幅的能力缩减。

---

#### H4. `executor.py`：cancel 与 interrupt 语义变更 + 类型安全丢失

**文件**：`backend/packages/harness/deerflow/subagents/executor.py:267-309`

此前，通过 `cancel_event`（同进程）取消的 subagent 会调用 `session.mark_cancelled()`。现在，当 cancel_event 触发但 cancel_marker 未触发时，改为调用 `session.mark_interrupted()`。只有跨进程的 cancel marker 才会调用 `mark_cancelled()` + `clear_cancel_marker()`。

这意味着：

- 通过 Gateway API 取消的任务 → `mark_cancelled()`（用户意图）
- 内部取消事件（超时、关闭等） → `mark_interrupted()`（可恢复）
- health monitor 的 reactivation 逻辑对 "interrupted" 和 "cancelled" session 的处理不同

同时，`_health_monitor` 的类型从 `SubagentHealthMonitor | None` 改为 `object | None`，丢失了所有类型安全。

**风险**：cancel/interrupt 的语义被显著改变。interrupted 的任务现在可以被恢复，而此前它们被视为 cancelled。

---

#### H5. `agent.py`：捕获 `ModuleNotFoundError` 以静默跳过 middlewares

**文件**：`backend/packages/harness/deerflow/agents/lead_agent/agent.py:293-299`

```python
try:
    middlewares = build_lead_runtime_middlewares(lazy_init=True)
except ModuleNotFoundError as exc:
    if "deerflow.agents.middlewares" not in str(exc):
        raise
    middlewares = []
```

这会捕获 `ModuleNotFoundError` 并返回空的 middlewares 列表。虽然它过滤了模块名，但可能掩盖真实的缺失依赖（比如某个 middleware import 的 pip 包缺失）。检查是匹配 `str(exc)` 中任意子串，比较宽泛。

**风险**：生产环境中 middleware import 路径拼写错误或缺失依赖会被静默忽略，而不是在启动时失败。

---

### Medium

#### M1. `task_tool.py`：已取消任务跳过逻辑变更

**文件**：`backend/packages/harness/deerflow/tools/builtins/task_tool.py:131-148`

对内存中 `get_background_task_result` 的 cancelled 状态检查被移除。现在只检查磁盘上的 session summary，并且跳过 `reason == "stale"` 的条目。这意味着：

- 被 stale session monitor 取消的任务现在可以在重试时重新执行
- 纯内存中的取消（session 还未持久化）不会被检测到

**风险**：中等。旧行为（检查内存）更保守。

---

#### M2. `main_session_middleware.py`：首次访问时从磁盘加载历史 ID

**文件**：`backend/packages/harness/deerflow/agents/middlewares/main_session_middleware.py:88-130`

新增 `_load_written_ids_from_disk()` 在首次访问时调用，读取整个 JSONL 文件以填充去重追踪器。对于大型对话文件（数千条消息），这会在每个 thread 首次访问时引入延迟。测试 `test_read_existing_jsonl_ids_on_init` 已正确更新以反映此行为。

**风险**：大对话历史的启动延迟。在当前规模下可能不影响，但值得留意。

---

#### M3. `session.py`：每次写入前都做防御性 `mkdir`

**文件**：`backend/packages/harness/deerflow/subagents/session.py:160-203`

`self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)` 现在在 `append_message()`、`_append_status_line()`、`write_summary()` 之前都会调用。此前目录创建由初始化代码处理。这会掩盖目录不存在的配置问题。

**风险**：较低，但会静默创建本不该存在的目录。

---

#### M4. `executor.py`：`_health_monitor` 丢失类型注解

**文件**：`backend/packages/harness/deerflow/subagents/executor.py:658`

```diff
-_health_monitor: "SubagentHealthMonitor | None" = None
+_health_monitor: object | None = None
```

这丢失了 health monitor 变量的所有类型检查。可能是为了避免测试中的 import cycle，但降低了类型安全。

**建议**：使用 `TYPE_CHECKING` guard + string annotation（此前就是如此）。

---

## 2. Suspicious but not confirmed

| 文件 | 疑点 |
|------|------|
| `session_monitor.py:347-375` | `_has_running_subtask` 现在显式跟踪 `has_thread_task`，有 task 但不是 running 时返回 `False`。此前会 fallback 到磁盘检查。新的 early return 是否正确？需确认。 |
| `session_monitor.py:376-392` | 新增了对没有 `.summary.json` 的"孤立" JSONL 文件的扫描。这些 JSONL 是否可能是仍有 background task 在跑的 session 创建的？可能错误地认为 "still running"。 |
| `session_monitor.py:433-469` | activation payload 结构变更（消息格式重构）。行为不变，但请目视确认。 |
| `process_manager.py:404-438` | `kill()` 重构把 status update 移到 lock 内。这实际上是一个 TOCTOU 竞态条件的 bug fix，但改动范围较大，应仔细确认。 |
| `prompt.py:272-274, 467-469` | 长 prompt 字符串被拆成多行。纯格式改动，但需确认运行时字符串完全一致。 |
| `session-status-dialog.tsx` | 移除了 `MainSessionCard` 中的 `useI18n` 和 `SubtaskRow` 的 `threadId`。两者似乎都未被使用，但值得验证。 |

---

## 3. Tests / Verification Gaps

| 缺口 | 为什么重要 |
|------|-----------|
| **`recovery.py` 自动恢复未测试** | 改动启用了此前禁用的自动恢复。没有测试覆盖实际的 `_notify_thread` 行为（使用 `runs.create` 而非 `update_state`）。 |
| **`input-box.tsx` `??` vs `||` 改动** | 无测试覆盖。disabled 逻辑的变更需要手动验证 streaming 时按钮的启用/禁用行为。 |
| **Cancel vs Interrupt in `executor.py`** | Interrupted task 的恢复流程应在 E2E 层面验证：通过 Gateway 取消的任务应标记为 "cancelled"，超时的任务应标记为 "interrupted"。 |
| **`multitask_strategy` 改为 `interrupt`** | 无集成测试验证活跃用户会话不会被意外 interrupt。 |
| **15 分钟默认超时** | 应验证无生产任务运行 15-20 分钟。 |
| **`_status_value()` helper** | 新的 `_status_value()` 函数在 `task_tool.py` 中用于将 enum value 与 string 比较。应测试使用 mock enum（无 `.value` 属性）的情况。 |
| **`mkdir` 改动 in `session.py`** | 无测试验证当目录结构错误时会发生什么（此前会 fail，现在会静默创建）。 |

---

## 4. Summary

本次 diff 混合了三类改动：

1. **纯格式/导入整理**（约 70% 的 diff）— 安全的 ruff/isort/行长度修复、删除未使用导入、重新格式化。这些都没问题。

2. **有意的语义变更，可能符合项目需求**（约 20%）— cancel vs interrupt 区分、task_tool 中的 stale skip 逻辑、基于磁盘的去重、process_manager 中 kill() 竞态修复。这些看起来是深思熟虑的设计选择，但显著改变了行为。

3. **为了让测试通过而引入的、有实际运行时风险的改动**（约 10%）— 这是最危险的类别：
   - **重新启用 auto-recovery**（`recovery.py`）— 该功能因特定原因被禁用
   - **`||` 改 `??` in `input-box.tsx`** — 对显式传 `disabled={false}` 是运行时 bug
   - **`multitask_strategy` 从 `reject` 改 `interrupt`** — 可能 interrupt 活跃用户会话
   - **降低默认超时和 max_turns** — 可能静默 break 长任务
   - **捕获 `ModuleNotFoundError` for middlewares** — 会掩盖真实问题

**最需要立即处理的是 C1（auto-recovery）和 C2（`??` vs `||`）**。H1-H5 也建议在合并前与团队确认是否为有意改动。
