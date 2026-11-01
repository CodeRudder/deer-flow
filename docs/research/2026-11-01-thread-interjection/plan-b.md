# 方案 B：真插话（Thread Interjection）详细设计

- 日期：2026-11-01
- 前置阅读：[research.md](./research.md)
- 状态：设计稿，未实现

---

## 1. 目标与非目标

### 目标

1. run in-flight 期间，用户发送的新消息**不丢失、不报错**，在下一个 step 边界注入正在执行的 agent 循环，影响其后续输出。
2. 前端发送时不弹 409 toast，改为乐观插入 + 注入确认。
3. IM 渠道忙时不再丢消息。
4. 注入消息在 journal、token 统计、message_processing 过滤链、memory 中有明确、一致的归属。

### 非目标

- 不支持把消息注入**已经在流式生成中的那一次 LLM 调用**（技术上不可行于当前 super-step；见 §7 竞态）。
- 不改变 run 生命周期模型（仍是单 run 单 astream，不引入多写者并发）。
- 不实现"撤销/编辑已发出消息"。

## 2. 交互语义

### 2.1 注入时机 = step 边界

LangGraph 以 super-step 为执行单位：每个 super-step 开始时从 checkpoint 读取 state，节点执行完毕后写回。因此注入的正确姿势是在两个 super-step 之间写 state——即：

- LLM 调用 / tool 调用**进行中**到达的消息：进入 pending 队列**等待**；
- 当前 step（一个 model step 或一个 tools step）结束、下一 step 开始前：由消费者任务 `update_state` 注入，下一个 model step 的 prompt 中即包含该 `HumanMessage`。

### 2.2 UI 表现

- 用户发送后消息立即以"待注入"态出现在会话流（乐观插入，样式上带 pending 标记）。
- 后端确认注入（写入 checkpoint 成功）后，通过 StreamBridge 下发一条 `interjection_applied` 事件，前端移除 pending 标记。
- 若 run 在注入前收尾：消息自动转为**新 run 的首条消息**（见 §7.3），前端表现为"你的消息开启了新的回复"，不发 toast。

## 3. 架构

### 3.1 输入通道选型

两个候选：

| 选项 | 做法 | 评估 |
| --- | --- | --- |
| RunManager record 加 `asyncio.Queue` | `RunRecord` 增加 `input_queue: asyncio.Queue`，worker 启动 run 时一并创建，消费者任务在同一 event loop 中运行 | **选中**。生命周期与 run 严格绑定（run 结束即弃），无需广播语义，改造面小 |
| StreamBridge 扩展 | 给 StreamBridge 加入站方法 | StreamBridge 是**纯出站**抽象（`runtime/stream_bridge/base.py:41-78`），加入站会破坏单一职责，且其生命周期（SSE 连接）短于 run、可能多连接共享，不适合承载"每 run 一个"的输入队列 |

**决策：RunManager record 加 `asyncio.Queue`。** 理由：插话输入是 per-run、单消费者语义，与 StreamBridge 的多订阅者出站扇出模型不匹配；且 SSE 连接断开重连不应影响已排队消息。

```python
# runtime/runs/manager.py（伪代码）
@dataclass
class RunRecord:
    ...
    input_queue: asyncio.Queue[Interjection] = field(default_factory=asyncio.Queue)

@dataclass
class Interjection:
    message: HumanMessage
    source: Literal["web", "im"]
    seq: int            # 用于多条插话保序
    applied_event: asyncio.Event | None  # 前端确认用
```

### 3.2 API 形态

**决策：`POST /runs/stream` 带新消息且 thread in-flight → 自动转插话（推荐），同时保留显式 `action=interject` 供 IM/测试使用。**

理由：前端与 IM 都已"原样发消息"，隐式转换让所有渠道零改动获益；`action=interject` 作为兜底显式契约。

```python
# api/routers/thread_runs.py（伪代码）
async def create_run(req: CreateRunRequest):
    record = manager.find_inflight(thread_id)
    if record and req.action != "interrupt":
        if not config.interjection_enabled:
            return 409  # 开关关闭时保持现行为
        await record.input_queue.put(Interjection(
            message=HumanMessage(req.input), source=req.source, seq=next_seq()))
        return 202 {"status": "interject_queued", "run_id": record.run_id}
    ...  # 原有 create_or_reject 路径
```

### 3.3 注入实现：消费任务 + `update_state`

worker 启动 run 的同时拉起一个消费者任务：

```python
# runtime/runs/worker.py（伪代码，围绕 :351 的 astream 启动处）
async def _consume_interjections(agent, config, queue, done):
    while not done.is_set():
        interjections = await _drain_batch(queue, first_wait_timeout=0.5)
        if not interjections:
            continue
        await agent.update_state(
            config,
            {"messages": [i.message for i in interjections]},  # 合并同批
            as_node="model",
        )
        for i in interjections:
            journal.record_interjection(i)        # 见 §6
            i.applied_event and i.applied_event.set()
        stream_bridge.emit("interjection_applied", [i.seq for i in interjections])

# run 主循环
done = asyncio.Event()
consumer = asyncio.create_task(_consume_interjections(agent, config, record.input_queue, done))
async for ev in agent.astream(input, config, stream_mode=...):
    ...
done.set()
await consumer
await _flush_remaining(record.input_queue)    # 见 §7.3
```

### 3.4 注入点节点名：`as_node="model"`

已核实（只读）`backend/packages/harness/deerflow/agents/lead_agent/agent.py`：lead agent 由 `langchain.agents.create_agent` 构建（`agent.py:535` bootstrap 分支、`agent.py:564` 默认/自定义 agent 分支），`state_schema=ThreadState`（`thread_state.py:206`，继承 `langchain.agents.AgentState`，`messages` 带 `add_messages` reducer）。

`create_agent` 生成的图只有两个节点：**`model`** 与 **`tools`**——没有自定义 `human` 节点。因此：

- **注入节点名取 `as_node="model"`**：`add_messages` reducer 只做追加，不关心 as_node 的语义归属；选 `model` 而非 `tools` 是为了让 checkpoint 的 `__prev__/__next__` 元数据指向 model 节点路径，下一次 super-step（无论 model 还是 tools）从该 checkpoint 读取时都能看到新消息。
- 若未来引入显式 human 节点，应改为 `as_node="human"` 并在本设计处同步更新。

注意：`update_state` 不会唤醒正在阻塞的 `astream` —— 消息在下一次 super-step 读取 state 时自然可见，这正是 §2.1 的"step 边界注入"语义，无需额外同步原语。

## 4. 前端行为

- 发送消息时先查 run 状态（已有 in-flight 则走同一条 `POST /runs/stream` 接口）：
  - 收到 `202 interject_queued` → 乐观插入消息（pending 样式），等待 `interjection_applied` SSE 事件转正式态；
  - 收到 201（新 run）→ 现行为不变；
  - 收到 409 → 仅在开关关闭的灰度场景出现，保留现 toast。
- `use-chat-page.ts:447-449` 的 disabled 逻辑维持不变（运行中输入框保持可用，这正是插话的前提）。
- 流式渲染期间插入的 user 气泡应打乱"AI 正在输入"的占位而不打断已渲染内容。

## 5. 数据一致性

| 关注点 | 处理 |
| --- | --- |
| **journal** | `update_state` 写入不经过 on_chat_model callback，journal 不会自动记录。消费者任务在注入后显式写一条 `interjection` journal 记录（含 `seq`、`source`、消息 id），保证回放/审计完整 |
| **message_processing 过滤链** | 前端/IM 的消息过滤逻辑按 sender/消息类型工作；插话消息是标准 `HumanMessage`，需确认过滤链不会把它误判为重复的用户输入或过滤掉（重点核对 `message_processing` 中按"本次 run 输入"裁剪的逻辑——插话消息不在 run 的初始 input 里，属于 state 追加，理论上不受影响，但需在单测中覆盖） |
| **token 统计** | 插话消息的 token 计入其被消费的那个 model step（自然发生，LLM 调用本身可见）；无需单独计费，但 journal 的 `interjection` 记录应关联该 step id 以便归因 |
| **memory 队列** | memory flush hook（`lead_agent/agent.py` 中 `memory_flush_hook`）按最终 state 的 messages 工作，插话消息已进入 state，自然纳入 memory；无需额外处理，仅需 E2E 验证不破坏去重 |

## 6. 开关与灰度

```yaml
# config.yaml
interjection:
  enabled: false          # 总开关，false 时保持 409 现行为
  max_pending: 8          # per-run 队列上限，超过直接 429
  im_channels: false      # IM 渠道独立灰度（B2 阶段打开）
```

灰度顺序：内部环境 → web 前端 → IM 渠道。开关关闭时 `create_or_reject` 行为与现状完全一致，可随时回退。

## 7. 竞态与边界

### 7.1 LLM 流式中注入

消息只能在 step 边界生效。若在 model step 执行中途到达：

- 排队等待当前 step 自然结束 → 注入 → 下一个 model step 可见。
- **不做**取消重试当前 LLM 调用（那是方案 C 的语义）。
- UX 代价：长 tool 调用（如深度搜索）期间插话可能等待数十秒——前端 pending 态需展示"将在当前步骤结束后生效"。

### 7.2 连续多条插话

- 消费者按 `_drain_batch` 一次取空队列并**合并为一次 `update_state`**（一个 messages 数组），避免多次 checkpoint 写放大。
- 保序：`seq` 单调递增，同一 HTTP 侧到达顺序即注入顺序。
- 超 `max_pending` 返回 429，提示用户稍后。

### 7.3 run 收尾时未消费消息

消费者在 `done.set()` 后做最后一次 `_flush_remaining`：

- 若队列非空且 run 正常结束 → 消息**自动转为新 run 的首条输入**，走现有 `create_run` 路径（复用方案 A 的 enqueue 语义），并通过 SSE 通知前端"已开启新回复"。
- 若 run 以 error/cancel 结束 → 消息同样转新 run（用户意图仍应被响应）；仅在 thread 已被删除等极端情况下退回 409 语义。

### 7.4 其他边界

- **子代理运行中**：插话只注入 lead agent 的 state（B1）；子代理循环中的 update_state 属于其自身 checkpoint，不混入（B2 再设计 subagent 可见性策略）。
- **interrupt 停止态**：`__interrupt__` 停止态下队列无人消费，消息应走 `command.resume` 路径而非插话（复用 `services.py:494-498` 现有逻辑）。
- **checkpoint 冲突**：`update_state` 与 astream 写 checkpoint 都走同一 checkpointer 的串行化，无并发写风险；但注意 update_state 使用与 astream 相同的 `config`（thread_id 一致）。

## 8. 风险清单

| # | 风险 | 等级 | 缓解 |
| --- | --- | --- | --- |
| 1 | `as_node="model"` 注入后 graph resume/回放语义异常（checkpoint 元数据把插话归为 model 输出） | 中 | 单测覆盖：注入后 `get_state` 的 messages 顺序与 `next` 指向；必要时引入薄 human 节点 |
| 2 | message_processing / 前端渲染把插话消息重复渲染或过滤 | 中 | E2E 覆盖运行中插话场景（§9） |
| 3 | 长 tool 调用期间插话延迟过长，用户重复发送 → 队列膨胀 | 低 | `max_pending` + 前端 pending 态提示 + 重复消息去重提示 |
| 4 | run 收尾 flush 与新 run 创建之间的竞态（两个触发源） | 中 | flush 与新 run 创建收敛到消费者任务单点串行 |
| 5 | token 统计 / journal 归因混乱 | 低 | journal 显式 interjection 记录关联 step id |
| 6 | IM 渠道 ack 语义：202 后 IM 侧认为已响应，但注入可能延后 | 低 | IM 侧文案"已收到，将在当前步骤后生效" |

## 9. 测试计划

### 单元测试

1. 注入后下一个 model step 可见：fake model 断言收到的 messages 包含插话 `HumanMessage`。
2. `as_node="model"` 注入后 `get_state` 正确：messages 追加、`next` 指向合理节点。
3. `None` user / 匿名消息注入的权限与过滤行为。
4. 竞态：LLM 调用进行中 `put` 消息 → 断言其在当前 step 结束后才被消费，且不中断流式事件序列。
5. 批量合并：3 条插话到达 → 仅 1 次 `update_state`、journal 3 条 interjection 记录。
6. 收尾 flush：astream 正常结束后队列残留消息 → 生成新 run 且首条输入正确。
7. 开关关闭：in-flight 发消息仍返回 409。

### E2E 测试

- 移动端：发起长回复 → 运行中插话 → 断言 pending 态出现、`interjection_applied` 后转正式态、最终回复参考了插话内容。
- IM 渠道（B2 后）：忙时发消息不再丢失，收到"将在当前步骤后生效"回执。

## 10. 分阶段落地

### B1 —— 最小可用（lead agent，无子代理）

- `RunRecord.input_queue` + 消费者任务 + `update_state(as_node="model")` 注入。
- `POST /runs/stream` 隐式转插话 + `action=interject`。
- journal interjection 记录；`interjection.enabled` 开关（默认 off）。
- 前端：202 处理 + 乐观插入 + `interjection_applied` 转正式态。
- 收尾 flush → 转新 run。

### B2 —— 子代理与 IM 渠道

- subagent 运行期间的插话策略（挂起至 lead agent step 边界 vs 转发，倾向前者，最小惊讶）。
- IM 渠道 `manager.py:1331-1349` reject 策略改插话 + 忙时队列化（`manager.py:64, 176-182`），带 ack 文案；`im_channels` 子开关灰度。

### B3 —— UI 实时态

- pending → applied 的过渡动画与"将在当前步骤后生效"提示。
- 连续插话的合并展示；队列接近上限提示。
- 观测：注入延迟（到达→applied）指标上报。

## 附：关键代码索引

| 关注点 | 位置 |
| --- | --- |
| lead agent 构建（`create_agent`，节点为 `model`/`tools`） | `backend/packages/harness/deerflow/agents/lead_agent/agent.py:535, 564` |
| `ThreadState`（继承 `langchain.agents.AgentState`，`messages` 带 `add_messages`） | `backend/packages/harness/deerflow/agents/thread_state.py:206` |
| run 接纳策略 / 待扩展 enqueue 分支 | `backend/packages/harness/deerflow/runtime/runs/manager.py:555-633`（`:571`） |
| astream 启动处（消费者任务挂载点） | `backend/packages/harness/deerflow/runtime/runs/worker.py:351` |
| StreamBridge（纯出站，不改） | `backend/packages/harness/deerflow/runtime/stream_bridge/base.py:41-78` |
| resume 路径（interrupt 停止态复用） | `backend/packages/harness/deerflow/api/services.py:494-498` |
| enqueue schema（已声明未实现） | `backend/packages/harness/deerflow/api/routers/thread_runs.py:97` |
| IM 渠道策略 | `manager.py:1331-1349`，`:64, 176-182` |

## 补充：排队/插话消息的取消与撤回（2026-11-01）

对齐 Claude Code「↑ 撤回排队消息」体验的取消语义：

| 状态 | 可否取消 | 行为 |
|---|---|---|
| Pending（排队未注入） | ✅ | 从 per-run 队列移除；前端气泡转"已撤回"或"撤回到输入框"（↑ 语义：文本回填输入框可编辑重发） |
| 已注入（step 边界已 update_state） | ❌ | 已进 LLM 上下文，不可撤回 |
| Run 收尾残留（未消费） | ✅ | 丢弃，不转新 run |

API：排队消息创建即分配 `interjection_id`（与 run_id 解耦）；
`DELETE /api/threads/{id}/interjections/{interjection_id}` → 204；已消费 → 409（对齐 run cancel 幂等口径）。
前端：排队气泡带撤回按钮；↑ 等价于"撤回并回填输入框"。
