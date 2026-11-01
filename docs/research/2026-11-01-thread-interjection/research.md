# 插话（Thread Interjection）支持调研报告

- 日期：2026-11-01
- 分支：`feat/gateway-media-api`
- 范围：仅调研与设计，不改代码
- 结论速览：**deer-flow 当前不支持插话**。run in-flight 期间向同一 thread 发送新消息会被拒绝（409）或打断当前 run。本文给出五维度证据（含文件:行号）、三个候选方案对比，并推荐落地路径 **A（排队）→ B（真插话）**。

---

## 1. 结论

当某个 thread 上已有一个 run 正在执行时，用户再次发送消息：

- HTTP 通道：`create_or_reject` 返回 **409 Conflict**，前端弹 toast，消息丢失。
- IM 渠道：策略硬编码为 `reject`，忙时**直接丢弃**新消息。
- 唯一的"打断"路径是 `interrupt` / `rollback` 策略——终止当前 run 而非注入输入。

不存在任何"新消息进入正在执行的 agent 循环"的通路：无输入注入点、StreamBridge 纯出站、`command.resume` 只服务于停止态。

## 2. 五维度证据

### 2.1 Run 接纳策略：create_or_reject

`backend/packages/harness/deerflow/runtime/runs/manager.py:555-633`

`create_or_reject` 在已有 in-flight run 时按策略三分支：

- `reject` → 409；
- `interrupt` / `rollback` → 打断当前 run 后新建。

enqueue 的 schema 虽然声明于 `backend/packages/harness/deerflow/api/routers/thread_runs.py:97`，但 `manager.py:571` 的策略分派只实现了 `reject / interrupt / rollback`，`enqueue` 会落入 **501 Not Implemented**。即"排队"停留在 API 契约层，从未实现。

### 2.2 执行模型：每 run 独立 astream，无输入注入点

`backend/packages/harness/deerflow/runtime/runs/worker.py:351`

每个 run 通过独立的 `astream` 驱动图执行。astream 循环旁没有任何并发消费用户输入的通道——图启动后，输入只能来自 checkpoint 中已存在的 state。

### 2.3 流桥：纯出站

`backend/packages/harness/deerflow/runtime/stream_bridge/base.py:41-78`

`StreamBridge` 只有事件出站方向（run → SSE/IM），没有任何入站方法可以把新消息送进执行中的 run。

### 2.4 resume / state 写入均不能作用于 in-flight run

- `command.resume`：仅用于 `__interrupt__` 停止态上启动新 run（`backend/packages/harness/deerflow/api/services.py:494-498`）。in-flight run 不经过 interrupt 边界，resume 无从谈起。
- `threads.py:887-960` 的 thread state 写入路径只改持久化 state，**不影响正在执行的 astream**（执行循环不会重读被外部覆盖的 state，除非跨 super-step；该路径也未与 in-flight run 建立任何同步）。
- `ClarificationMiddleware`（`backend/packages/harness/deerflow/agents/middlewares/clarification_middleware.py:74-112`；前端对应 `use-human-input.ts`）是**停-答-重启**：主动 interrupt 停住、等用户答、再 resume 新 run——不是插话。

### 2.5 前端与 IM 渠道表现

- 前端运行中输入框保持可用：`use-chat-page.ts:447-449` 的 disabled 条件未包含 `isLoading`——但发送后只会得到 409 toast，消息内容丢失。
- IM 渠道：`manager.py:1331-1349` 硬编码 `reject` 策略；`manager.py:64, 176-182` 忙时丢消息且无排队/回执。

## 3. 三个候选方案

| 方案 | 思路 | 工作量 | 关键改动点 | 代价/风险 |
| --- | --- | --- | --- | --- |
| **A. enqueue 排队** | in-flight 时新消息进 per-thread pending 队列；run 结束后自动开下一个 run | **小** | `create_or_reject` 支持 `enqueue`（manager.py:571 补分支）+ in-flight `finally` 触发下一个 + IM 侧 strategy 改 enqueue | 消息不能影响当前 run 的输出，只是"接力" |
| **B. 真插话** | 输入队列 + astream 循环旁并发消费，在 step 边界用 `agent.update_state(..., as_node=...)` 注入 `HumanMessage`，下一个 model step 即可见 | **中** | StreamBridge/RunManager 加输入队列；注入点节点名；journal/token 统计；message_processing 过滤链；竞态（LLM 调用中消息等当前 step 结束） | 需处理图内部 state 一致性、token 计费归属 |
| **C. interrupt-resume** | `abort_event` 触发 interrupt 保 checkpoint + `command.resume` 开新 run | **小-中** | 复用现成 interrupt/resume 组件 | 打断当前 LLM 调用（费用浪费、响应体验差），语义上是"打断重答"而非插话 |

详细设计见同目录 [plan-b.md](./plan-b.md)。

## 4. 推荐路径：A → B

1. **先落地方案 A（enqueue）**：工作量最小，立刻消除"409 / 丢消息"的用户痛点；同时引入 per-thread pending 队列这一基础设施，为 B 复用。
2. **再演进出方案 B（真插话）**：在队列之上把"消费时机"从 run 边界前移到 step 边界，使消息能影响正在进行的回答。

方案 C 不推荐作为主线：打断当前模型调用既浪费已产生的 token，体验上也等价于让用户"重新问一遍"。

## 5. 附：关键代码索引

| 关注点 | 位置 |
| --- | --- |
| run 接纳策略 | `backend/packages/harness/deerflow/runtime/runs/manager.py:555-633`（`create_or_reject`），`:571`（策略分派缺 enqueue） |
| enqueue schema | `backend/packages/harness/deerflow/api/routers/thread_runs.py:97` |
| run 执行 | `backend/packages/harness/deerflow/runtime/runs/worker.py:351` |
| StreamBridge | `backend/packages/harness/deerflow/runtime/stream_bridge/base.py:41-78` |
| resume 语义 | `backend/packages/harness/deerflow/api/services.py:494-498` |
| thread state 写入 | `backend/packages/harness/deerflow/api/routers/threads.py:887-960` |
| 澄清中间件 | `backend/packages/harness/deerflow/agents/middlewares/clarification_middleware.py:74-112` |
| 前端输入框 | `use-chat-page.ts:447-449`；`use-human-input.ts` |
| IM 渠道 | `manager.py:1331-1349`（硬编码 reject），`:64, 176-182`（忙时丢消息） |
