# 会话过程展示重构 — 时间线交互方案

- 日期：2026-11-03
- 分支：`feat/gateway-media-api`
- 状态：方案（仅设计，未实施）
- 范围：`frontend/src/components/workspace/messages/`、`frontend/src/core/messages/`、`frontend/src/core/threads/`（含桌面端与移动端 chat 页）

## 0. 设计约束（已拍板，不可选项）

1. 过程内容（思考 / 工具调用）**不放右侧栏**——右侧栏继续只承载产物 / 文件浏览。
2. 默认**折叠紧凑显示**：每个步骤单行；点击展开细节，再点收起。
3. **不使用大卡片**：现有的整块折叠卡（`ChainOfThought` 外框 `rounded-lg border p-0.5` 的 processing 面板）废除。
4. 用户输入与**插话必须出现在时间线的正确时序位置**，不允许全部堆在过程块之后。

---

## 1. 现状与问题

### 1.1 现有渲染结构

数据层：`core/messages/utils.ts` 的 `getMessageGroups()` 把消息流分成 6 类 group：
`human` / `assistant`（气泡）/ `assistant:processing`（过程）/ `assistant:clarification` / `assistant:present-files` / `assistant:subagent`。

- **过程打包成大块**：所有连续的中间 AI 消息（reasoning + tool_calls + 未定文本）累积进**一个** `assistant:processing` group（`utils.ts` L155-169），整组交给 `MessageGroup`（`message-group.tsx`）。
- `MessageGroup` 内部 `convertToSteps()` 展开成 `reasoning | toolCall | assistantText` 三类 step，再用 Vercel AI elements 的 `ChainOfThought` 面板渲染：**外层带边框圆角的整块卡片**（`"w-full gap-2 rounded-lg border p-0.5"`），内部还有"展开更早步骤"按钮、末尾 thinking 折叠区等二级结构。
- 移动端走 `collapsedSteps` 分支：一张"ran N steps · M tools"折叠卡 + 独立的答案区——但折叠的仍是**一整组**，不是每步独立。
- **答案与过程的位置**：`assistantText` step 要么混在面板内、要么（移动端）全部排在折叠卡**之后**；中途的阶段性文字输出和工具步骤的时序被打平。
- **插话**：`pendingInterjections` 在 `message-list.tsx` L927-957 渲染，且是**整张消息列表末尾统一追加**——排队气泡、已注入气泡全部堆在最后一条 group 后面，与它真正被注入的时序位置脱节（与约束 4 冲突）。
- 插话生命周期在 `core/threads/interjection.ts` + `hooks.ts`：`POST /interjections`(202) → SSE `interjection_applied`（flip `applied`）→ 注入的 HumanMessage 落入 transcript 后按文本匹配 `pruneLandedInterjections` 剪掉气泡；run 结束未消费的由 worker 合并成一个新 run，SSE `interjection_auto_run` 标记 `auto_run_run_id` 并 `joinStream`。

### 1.2 问题清单

| # | 问题 | 违反约束 |
| - | ---- | -------- |
| P1 | 过程内容被打包进带边框大卡片，视觉重、占屏大 | 约束 3 |
| P2 | 折叠粒度是"整组"，不是"每步"；桌面端默认全展开 | 约束 2 |
| P3 | 中间阶段性 assistantText 与工具步骤时序错位（答案被挪到过程之后） | 约束 4 |
| P4 | 插话气泡固定渲染在列表尾部，不按注入点排布 | 约束 4 |
| P5 | `aboveLastToolCallSteps` 折叠旧步骤、只留最后一个工具调用的"上下两段"逻辑复杂，历史 run 翻阅体验差 | 约束 2/3 |
| P6 | `convertToSteps()` 把一次 run 的多轮 reason→act→observe 链拍平成线性 step 列表，丢失"第 N 轮"结构 | 结构性问题 |

---

## 2. 时间线结构

### 2.1 统一时间线元素

新模型：把 `Message[]` 直接归一成**时间线条目（TimelineEntry）流**，替代现在的 group → MessageGroup 两级结构中"过程大块"的那一档。

```
type TimelineEntry =
  | { kind: "human";        message: Message; ts }
  | { kind: "interjection"; item: PendingInterjection; anchorRunId; anchorSeq }
  | { kind: "step";         step: Step; roundIndex }        // 单行紧凑步骤
  | { kind: "answer";       message: Message; streaming }   // 答案流式块（assistant 气泡）
  | { kind: "artifact";     group; }                        // present-files / clarification 卡 / subtask 卡
```

排布规则（按 transcript 顺序，单调不回退）：

1. **human 消息**：一条 HumanMessage 一个条目，位置即它在 transcript 的下标。隐藏消息（`hide_from_ui`、clarification 应答）跳过，不产生条目。
2. **插话气泡**：见 §5。
3. **step 条目**：AI 消息中的每个 reasoning / tool_call 生成一个独立 step 条目；`assistantText` **不**生成 step——它要么是 answer 条目（turn 收尾），要么是中间阶段输出（见规则 5）。`task` 工具调用仍走 `assistant:subagent` 卡（保持现状），`ask_clarification` 仍走 clarification 卡。
4. **answer 条目**：一条 content-only AI 消息（`becomesAssistantBubble` 条件，沿用 #3868/#4304 语义）成一个 answer 块；流式中未定的文本（`isUnresolvedAssistantText`）先以 answer 流式块呈现，若后续同消息追加 tool_call，则该块**降级**为 step（其 reasoning/text 归入该 step 的展开详情）。
5. **中间阶段输出**（同一轮 reason→act 之间模型吐出的文字）：以"无图标的一行弱化文本 + 可展开"的 step 变体呈现（`kind: step, variant: "narration"`），保持在时序位置上，不再挪到过程块后。
6. **artifact 条目**：present-files、clarification 问答卡、subtask 卡，保持现有独立形态，按消息位置插入时间线。

### 2.2 run 内多轮

一次 run 内 LangGraph 循环产生的消息序：`AI(reason+tool_calls) → Tool → AI(reason+tool_calls) → Tool → … → AI(content)`。时间线不做归组，直接顺序展开：

```
[human] 用户输入
[step]  ◦ 思考…（第1轮）          ← 单行，点击展开思考全文
[step]  ✓ web_search "query"      ← 点击展开入参/出参
[step]  ◦ 思考…（第2轮）
[step]  ✓ read_file path
[step]  ◦ 中间阶段说明文字（narration 变体）
[step]  ✓ write_file draft.md     ← 点击可跳产物（保留现有 ARTIFACT_STEP_NAMES 跳转）
[answer] 答案流式块 / 最终气泡
[artifact] present-files 文件列表
```

### 2.3 多 run 线程

线程就是多条 run 首尾相接。时间线是**全局一条**，不按 run 分段渲染；run 边界天然由 human 条目（新一轮输入）或 auto-run 的合并输入体现。旧 run 的 step 条目保持折叠收起态（默认 ✓），新 run 从当前播放位置继续。为了支持"滚动到上一轮"时不过度渲染，折叠态单行是轻节点，无需虚拟化即可承受数百行（如超出再议虚拟化，见 §8 决策点）。

---

## 3. 步骤条交互

### 3.1 单行紧凑形态（收起态，默认）

```
[icon] [一句话摘要]······························· [状态符]
```

- 高度约 28–32px，行内 `text-sm`，图标 16px（lucide，沿用现有 `ToolCall` 的图标映射：Search/Globe/Folder/BookOpen/NotebookPen/SquareTerminal/ListTodo/Wrench/Lightbulb）。
- **状态符**（行尾右对齐，等宽小字号）：
  - `✓` 完成（muted 色）
  - `▶` 当前进行中（spinner：`Loader2Icon animate-spin`，或呼吸的点）
  - `✗` 失败/中断（`text-destructive`）
  - 运行中且无结果的 web_search 可附已耗时（沿用 `turnStartTime` 思路，可选）。
- **摘要文案来源**：完全复用现有 `ToolCall` 的 label 逻辑（`args.description` → 工具专属模板如 `searchOnWebFor(query)` → `useTool(name)` 兜底），不新增后端字段。reasoning 步骤摘要 = 思考文本首行截断（~60 字符）；narration 变体 = 文本首行。
- 无边框、无背景——**行本身不是卡片**；仅 hover 时给 `rounded hover:bg-accent/50` 提示可点。

### 3.2 展开态

- 整行可点（`aria-expanded`），点击展开该步骤细节，再点收起。各 step 独立状态（`Set<stepId>` 展开集合放在 Timeline 容器，避免行级 state 在流式更新时丢失）。
- 展开内容：
  - reasoning 步骤 → 思考全文（`MarkdownContent`，复用现有渲染）；
  - toolCall 步骤 → 入参（格式化 JSON / bash 命令 CodeBlock）+ 出参（`findToolCallResult`，保留 web_search 结果列表、image_search 缩略图网格、`[base64 omitted]` 降级提示）；
  - narration 步骤 → 全文 markdown。
- **展开态视觉边界（轻量，非卡片）**：
  - 左边线：展开区 `border-l-2 border-border/60 pl-3 ml-2`，与步骤行同缩进起点；
  - 缩进：展开区相对时间线主轴再缩进一级（与父行图标右缘对齐），不新增左右内边距外框；
  - 背景至多 `bg-muted/40`（可选，默认透明）；**禁止** `border rounded-lg p-4` 形态。
- 动画：高度过渡 120ms，或直接无动画（移动端建议无动画以省性能）。
- 例外保留：artifact 步骤（write_file / str_replace）行内点击仍是"打开产物"跳转（现有 `select()+setOpen(true)`），展开细节改为行尾独立的 chevron 热区，避免点击语义冲突。

---

## 4. 流式更新规则

- **新步骤追加**：SSE 推进使 `messages` 尾部新增 AI/Tool 消息时，时间线在当前播放位置**追加新 step 行**（key = tool_call.id / message.id，与现在一致），已有行不重渲染内容。
- **当前步骤 ▶**：最新一个尚无对应 ToolMessage 的 toolCall 行 = ▶（数据源即 `findToolCallResult` 落空）；reasoning 行流式期间显示打字态（首行截断文本随流增长）。
- **答案流式块与步骤条相对位置**：answer 块永远位于当前 run 所有已产生 step 之后（时序天然如此，无需特殊逻辑）。流式期间 answer 块底部跟随现有的 "thinking placeholder"（`hasActiveAssistantText` 逻辑保留），但 placeholder 形态也收敛为单行 spinner 行。
- **完成收敛**：run 结束（`isLoading=false`）→ ▶ 全部落定为 ✓/✗；未定文本块定型为 answer 或降级为 step（§2.1 规则 4）；中间折叠不发生——所有行保持单行收起态，仅当前刚展开的行维持展开。
- **回归防护**：#4304（content-only AI 消息后续追加 tool_call）在流式期间表现为 answer 块降级为 step——需要一次性"块→行"的形态切换，允许内容闪变，但不允许丢失；用 `message.id` 作 key 保证 React 复用。

---

## 5. 插话排布

### 5.1 排队气泡挂在哪

- 发送即渲染：排队气泡立即出现在**时间线当前末尾**（即当前 run 播放位置的下方、右对齐 user 气泡样式），带 "已排队" 提示 + Undo（现状交互保留）。
- 这与现状渲染位置一致（列表末尾），因为排队时 run 还没产生注入点。

### 5.2 生效后移到注入时序位置

- SSE `interjection_applied` 到达：后端在同一 run 内把 HumanMessage 注入 transcript，注入位置 = 其后所有 AI/Tool 消息之前。前端不再把气泡留在末尾，而是**按注入的 HumanMessage 在 transcript 中的下标**定位：气泡条目渲染为该 HumanMessage 的时序位置（实现上直接让气泡"归位"到该消息下标处，随后 `pruneLandedInterjections` 按文本匹配剪掉临时气泡，逻辑不变——视觉上等价于气泡原地变成真实 user 气泡）。
- 因此需要把 `pendingInterjections` 的渲染从"列表尾部 map"改为进入 Timeline 归一器：优先尝试匹配已落地的 HumanMessage（下标定位），未匹配的排在当前末尾。

### 5.3 自动合并执行新 run 的气泡归属

- SSE `interjection_auto_run`：条目标记 `auto_run_run_id`；新 run 的合并输入 HumanMessage 落地后同样按文本匹配剪掉临时气泡（现有 `pruneLandedInterjections` 已覆盖）——时间线上最终呈现为：旧 run 末尾 → 一条 user 气泡（合并文本）→ 新 run 的步骤条/答案。UI 不为 auto-run 画额外分隔线（决策点见 §8）。

---

## 6. 兼容与边界

### 6.1 移动端

- 单行步骤条天然适配窄屏；展开区左边线在 390px 下保留（去掉背景色），tap 目标 ≥44px（行内 padding 撑高）。
- 移动端现有 `collapsedSteps` 的"ran N steps · M tools"汇总行**废除**，统一为逐行折叠；产物跳转入口不再靠"含 artifact 步骤则整组展开"，改为 artifact 行内直达跳转（语义反而更顺）。
- 右侧栏（移动端为 drawer overlay）继续只放文件/产物，不放过程（约束 1）。

### 6.2 旧消息（已持久化 checkpoint）

- 旧线程 reload 后走同一 Timeline 归一器：`getMessageGroups` 的输入是同构 `Message[]`，AI 消息无 streaming 态，全部步骤落定为 ✓。**兼容读路径必须保留一个版本周期**：归一器读旧数据不需迁移（checkpoint 里就是原始消息），风险仅在 `assistantText`/reasoning 归属规则变化导致历史渲染差异（如旧消息中 content+tool_calls 同体的消息：按现规则它不成为气泡、内容进 step；新规则保持一致即可）。建议加快照测试（现有 `tests/unit/...` 风格）固定若干历史形态。

### 6.3 response_sanitizer 输出形态

- `app/gateway/response_sanitizer.py` 会把大 base64 变成 `image_url_removed` content part / `[base64 omitted N KB]` 截断串 / `b64_json_omitted: true`。步骤展开区渲染工具出参时需识别这三类占位：`image_url_removed` 显示"内联图片已省略 (N KB)"灰字提示（可链到文件面板对应产物），不当作普通文本/JSON 渲染崩溃。

### 6.4 与产物/文件侧栏的联动

- 右侧栏（`workspace-file-panel` + artifacts）零改动：仍由 `useArtifacts` 驱动；时间线只保留 artifact 步骤行的 `select()/setOpen(true)` 跳转与 `write-file:` URL 约定（含 `message_id`/`tool_call_id` 查询参数）。
- `present-files` 条目仍是时间线上的 `ArtifactFileList`，与侧栏互不抢占。

### 6.5 其他保留语义

- clarification（`ask_clarification`）流程：问题气泡 + `HumanInputCard`/`HumanInputAnsweredRow` 位置不变；其 reasoning 进入时间线 step（现规则：面板保留 reasoning，不画问题文本）。
- `hide_from_ui` 隐藏消息、hidden control message（summary/todo_reminder 等）继续被过滤。
- token debug（`step_debug` 模式）：CoinsIcon 摘要行作为 step 变体保留在各消息首个 step 前。

---

## 7. 实现分层

### 7.1 数据层

| 改动 | 文件 | 大小 |
| ---- | ---- | ---- |
| 新增 Timeline 归一器 `buildTimelineEntries(messages, opts)`（吸收 `getMessageGroups` 的过程段职责 + `convertToSteps` + 插话定位） | `core/messages/timeline.ts`（新） | **M** |
| `getMessageGroups` 保留（clarification/subtask/present-files 分支仍用），仅移除 processing 段或标记 deprecated | `core/messages/utils.ts` | S |
| 插话条目定位辅助（按落地 HumanMessage 下标锚定） | `core/threads/interjection.ts` | S |

### 7.2 组件层

| 改动 | 文件 | 大小 |
| ---- | ---- | ---- |
| 新 `ProcessStepRow`（单行 + 状态符 + 展开区），吸收 `ToolCall`/reasoning 渲染与图标映射 | `components/workspace/messages/process-step-row.tsx`（新） | **L** |
| 新 `TimelineView`（渲染 entries 流；管理展开集合、▶ 指针） | `components/workspace/messages/timeline-view.tsx`（新） | M |
| `MessageList`：删掉尾部 `pendingInterjections` map，接 TimelineView；保留 clarification/subtask/present-files 分支 | `components/workspace/messages/message-list.tsx` | M |
| 废除 `MessageGroup` 的 ChainOfThought 大卡片路径（含 `CollapsedSteps`、`aboveLastToolCallSteps` 二段逻辑）；组件删除或收缩为兼容壳 | `components/workspace/messages/message-group.tsx` | M |
| 移动端四个 chat page：`collapsedSteps` prop 链路拆除 | `app/m/(app)/.../chats/[thread_id]/page.tsx`、`app/workspace/.../chats/[thread_id]/page.tsx`、agents 变体 ×2 | S |
| `chain-of-thought.tsx`（registry 生成件）：不再被 messages 引用（不手动改文件，仅移除引用） | `components/ai-elements/` | S |

### 7.3 测试

- 单测：`tests/unit/core/messages/timeline.test.ts`（归一器：多轮、多 run、#4304 降级、插话定位、sanitizer 占位识别）；`process-step-row.test.tsx`（展开/收起、状态符）。现有 `message-group`/`subtask-card` 相关用例迁移。
- E2E：现有流式 mock 用例改断言（大卡片 testid → 行级 testid）。

### 7.4 迁移风险

1. **流式回归**：#4304 的"块→行降级"与 #3868 的"reasoning 只渲染一次"是历史踩坑点；归一器需单测锁死。
2. **插话剪除竞态**：气泡归位依赖文本匹配；`pruneLandedInterjections` 语义不动则风险可控，但渲染位置变化可能暴露"applied 未剪除时气泡短暂双影"——归位逻辑需以 HumanMessage 存在优先。
3. **历史线程视觉 diff**：老消息渲染形态改变属预期，但需防内容丢失（快照测试兜底）。
4. **E2E 断言大改**：桌面+移动两套页面。

### 7.5 分期

- **MVP**：数据层归一器 + `ProcessStepRow` + `TimelineView` 接入桌面端；废除大卡片；插话仍渲染在末尾（不定位）。桌面先行验证交互。
- **第二期**：插话时序定位（§5.2/5.3）；移动端切换 + 拆 `collapsedSteps` 链路；sanitizer 占位渲染打磨。
- **第三期**：token debug step 变体接入、步骤耗时显示、（可选）长线程虚拟化。

---

## 8. 决策点清单（需用户/主会话拍板）

| # | 决策 | 选项 | 建议 |
| - | ---- | ---- | ---- |
| D1 | **步骤摘要文案来源** | a) 复用 `args.description`/现有模板（零后端改动）；b) 后端在 tool result/事件里补 summary 字段 | a，后端字段留待以后 |
| D2 | **reasoning 默认态** | a) 默认收起（单行截断摘要）；b) 流式期间自动展开最新一条思考、结束后收起；c) 全局设置项 | b（流式时可读性最好），收起为稳态 |

> **修订记录（2026-11-03，用户实测推翻 D2 建议）**：D2 落地为「所有思考/工具步骤一律默认折叠紧凑显示，**无任何自动展开**」，采纳选项 a。流式中的可读性改由 req-4 承担：进行中（最后一条 step）的折叠预览随流式滚动显示末尾几行（作用范围含最新 reasoning 行）；查看详情一律手动点击展开（展开态不变：格式化 + 10 行 clamp + 查看更多）。D7 失败步骤默认展开错误详情保持不变。实现：`TimelineView` 不再持有 auto-expanded 集合，`ProcessStepRow` 移除 `forceExpanded`。
| D3 | **中间 narration 文本** | a) 独立 step 行可展开；b) 直接并入 answer 流式块（若同消息后来加了 tool_call 再降级） | a，避免形态抖动 |
| D4 | **run 边界可视性** | a) 无分隔，纯时序；b) auto-run / 新 run 起点加一条细分割线或"继续执行"标签 | b 仅用于 auto-run（用户需要知道新任务开始） |
| D5 | **展开状态持久化** | a) 会话内存（切线程/刷新即收起）；b) localStorage 记住偏好（如"默认全部展开"开关） | a + 决策点 D6 的全局开关另议 |
| D6 | **是否提供"展开全部过程"开关** | a) 无；b) 头部一个切换（调试场景） | b 放第三期，非 MVP |
| D7 | **失败步骤呈现** | a) 仅 ✗ + 行内红字摘要；b) 展开默认自动打开错误详情 | a，点击看详情 |
| D8 | **长线程性能** | a) 不虚拟化先上线；b) 直接上虚拟列表 | a，实测再说 |
| D9 | **旧 `MessageGroup`/`collapsedSteps` API** | a) 直接删除（本仓库内无外部消费者）；b) 保留一个版本 deprecated | a，一次性切干净，快照测试兜底 |
