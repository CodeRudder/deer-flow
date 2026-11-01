---
name: drama-production
description: Use this skill when the user asks to produce a short drama (短剧) from an idea, outline, or novel/text source — full creation-agent pipeline over the Media Gateway (MGW) drama API. Four staged phases — 剧本编写 (LLM drafts mainline → episodes → chapters → plots → shots with dialogue, persisted into MGW; assets extracted into the reference tray with character cards as the consistency baseline), 五要素分镜 (per-shot action/expression/dialogue/scene/rhythm with <Picture N> citations validated by prompt_preview — never enter GPU with a failing design gate), 生成与 QC 迭代闭环 (per-clip submit with Idempotency-Key, QC attribution prompt/citation/seed, ≤3 auto-fix rounds, checkpoint vs auto mode), and 分集交付 (episode partition, artifacts served through the deer-flow proxy). All data lives in MGW; deer-flow keeps no domain tables, only a sidecar resume cursor. Read SKILL.md before replying — including before any clarifying question — for the phase gates, the five-element checklist, the QC loop rules, and the resume protocol.
---

# 短剧创作智能体 Skill（drama-production）

## Overview

本 skill 把一段创意 / 大纲 / 原著文本，变成一部可交付的短剧：剧本 → 五要素
分镜 → 逐镜生成 → QC 迭代闭环 → 分集交付。全部数据落在 **MGW（Media
Gateway）drama API** 上（剧→章→故事线→情节→片段 clip→镜头 shot 结构 +
资产托盘 + QC 结果 + 审计台账）；deer-flow 侧**没有**短剧/分镜领域表，
会话里只保留一个轻量 sidecar 文件记录"断点续作游标"。

设计依据：design.md §6.1/§7、workflow.md W3a、验收 acceptance.md A2b。
MGW 契约真源：`minimax-h3-gateway/docs/integration/api/drama.md`——所有
端点、字段、错误信封以它为准，**不要凭记忆猜接口**；动手前先查该文档
对应小节。

## 调用方式（provider 先行，与其他 skill 一致）

- 与 `video-generation` 的"脚本直调 provider"惯例对齐：本 skill 的编排逻辑
  走 harness 的 `deerflow.media_gateway` 包——HTTP 层用
  `MediaGatewayClient`（Bearer 令牌注入 / 统一超时 / `Idempotency-Key` /
  错误信封归一化），QC 闭环用 `deerflow.media_gateway.qc` 的
  `DramaQCClosedLoop`（设计层校验、归因分类、修正策略、版本记录，均为
  纯函数 + 依赖注入 client，可被 mock 驱动测试）。
- **令牌**：每个生成/写操作带**当前用户**的 `h3t_` 令牌（经
  `MediaGatewayProvisioner.ensure_user()` 置备，绝不使用 admin 令牌做用户
  操作——owner 隔离：A 建的剧 B 经任何 API 均不可见，跨 owner 读一律 404）。
- **幂等**：每个生成步骤带 `Idempotency-Key`（如
  `f"drama-{clip_id}-v{version}-try{n}"`），安全重试不重复计费/重生成。
- **零 GPU 先行**：设计层校验（`prompt_preview` + 五要素自检）不花 GPU；
  未过设计门的 clip 永远不进生成。
- 网关不可达 / 429 / 401 的降级语义与 `video-generation` 相同（透传
  `Retry-After`、不本地重试风暴），见 design.md「失败与降级」。

## 模式（对话开始时确认一次）

| 模式 | 行为 |
|---|---|
| **checkpoint**（缺省） | 阶段 1 结构落库前、阶段 2 分镜目检、阶段 3 每镜 QC 超限——三处等用户确认 |
| **auto** | 全程自动；QC 门控，每镜 ≤3 轮自动修正，超限标记 `needs_human` 并继续其余镜头 |

用 `ask_clarification`（`approach_choice`）确认：`["checkpoint 模式", "auto 模式"]`，
同时确认输入类型（创意 / 大纲 / 原著文本）与目标单集时长（缺省 60s）。

## 阶段 1：剧本编写（LLM 起草 → MGW 落库）

1. **LLM 起草**（在会话内完成，先给用户看，checkpoint 模式确认后再落库）：
   主线 → 分集大纲 → 章 → 情节 → 片段/镜头清单（含台词初稿）。
2. **结构落库**（逐层调 MGW，契约 drama.md §3–§10）：
   `POST /v1/drama` → `POST …/chapters` → `POST …/chapters/{ch}/storylines`
   → `POST …/plots` → `POST …/clips`（`budget_tier ∈ {1,5,10,15}`，即一次
   生成的镜头组）→ `POST …/shots`（每镜 `prompt_en` 必填、`prompt_zh` 是
   **主稿**、`dialogue` 每条必带 `speaker`——保存链会逐项拦截：
   `negative_wording` / `dialogue_unassigned` / `prompt_en_empty` /
   `clip_over_budget`，结构化错误信封为 `{"detail":{"error":{…}}}`）。
3. **资产抽取 → 托盘登记**：从剧本抽角色/场景/道具 →
   `POST /v1/drama/{did}/characters`（`subject_id` 为引擎一致性绑定号）等
   资产端点 → 参考图注册媒体后，经 `PATCH …/clips/{cl}` **全量回传**
   `ref_tray`（数组位置即 `<Picture N>` 编号；增/删/换图都回传整组，只回
   增量会被当成"删掉其余条目"）。
4. **角色卡 = 一致性基线**：同一角色跨所有镜头的分镜设计一律引用同一角色
   卡（同一 `media_id`）；改名/重排后 MGW 托盘重映射自动改写镜头正文里的
   `<Picture N>` token，不需要手工同步。

剧本事实的唯一存储在 MGW。deer-flow 侧只在工作区写 sidecar（见
「断点续作」）。

## 阶段 2：分镜五要素（逐镜填，先过目检再进 GPU）

对每个镜头，在 MGW shot 记录上补齐五要素（design.md §7.2）：

| 要素 | 落库位置 | 校验 |
|---|---|---|
| 人物动作 | `prompt_zh`（主稿）正文 | 清单 block 项：正文为空 → 拦 |
| 表情 | 正文情绪描述 / `expression` | 清单 warn 项：缺表情提示补充，不拦截 |
| 对话 | `dialogue`（`speaker` 必填） | 清单 block 项 + 服务端 `dialogue_unassigned` |
| 场景 | 托盘 `<Picture N>` 引用 + `camera`/`size`/`scene_id` | 清单 block 项：场景引用与机位/景别全空 → 拦 |
| 时长/节奏 | `duration_s`（计入片段预算） | 清单 block 项 + 服务端 `clip_over_budget` |

五要素清单是**可配置纯函数**
（`qc.DEFAULT_FIVE_ELEMENT_CHECKLIST` + `qc.design_verdict`）；逐镜填完后：

1. **cite 引用**：正文 token 用 `<Picture N>`（托盘编号），不本地拼装。
2. **`prompt_preview` 校验**（零 GPU）：`GET …/clips/{cl}/prompt_preview`，
   只认它的 `valid` 与 `checks`——`token_manifest_match=false`（编外 token）、
   负向措辞（「不要 X」会把 X 画出来，改写为正向描述）、场景链接悬空、
   托盘图未被引用等，逐项修正后重验。**valid=true 才进阶段 3**。
3. checkpoint 模式：把分镜板（逐镜五要素 + 预览 prompt）给用户目检。

## 阶段 3：生成与 QC 迭代闭环（每镜）

1. **提交**：`qc.DramaQCClosedLoop.run(fix_fn)` 驱动——先跑设计层校验
   （`check_design`：preview + 五要素），通过后
   `POST …/clips/{cl}/generate`（单笔全局租约；`Idempotency-Key` 齐备），
   轮询 `GET …/generate` 直到 QC 可读，`GET …/qc` 取结果。
2. **归因分类**（`qc.attribute_qc`，确定性优先级）：
   - issue 提及 `<Picture N>`/托盘/引用 → **citation**（引用错）；
   - `must_fix_storyboard=true` 或 identity/space 低分/一致性措辞 →
     **prompt**（设计缺陷）；
   - 闪烁/伪影/抖动等随机性措辞 → **seed**（换种子重抽，不改分镜）。
3. **修正策略**（`qc.remediation_plan`）：citation → 修托盘引用（PATCH clips
   全量回传触发重映射）；prompt → 改 shot 五要素字段；seed → 同设计换种子
   重抽（可选 seedmatch `/score` 择优）。**重出前必须先归档当前版本**
   （`POST …/clips/{cl}/archive`，否则 409 `previous_version_not_archived`）；
   修正后回设计层校验，再重新生成。每镜自动迭代 ≤3 轮（可配
   `max_iterations`）；超限 → checkpoint 模式回交用户 / auto 模式标记
   `needs_human` 继续其余镜头。
4. **版本记录**：每轮设计结论 / QC 原文 / 归因 / 修正动作追加进
   `DramaQCClosedLoop.history`（dict 列表）；是否写 sidecar 由调用方决定。
   MGW 侧 `archives` + 审计台账（`GET …/audit`）本身可追溯、可回滚到任一
   版本重生成。

fix_fn 由会话实现：接收"改什么"的决策，对 MGW 做 `PATCH shots` /
`PATCH clips`（ref_tray）/ archive 操作，返回变更摘要进历史。

## 阶段 4：分集交付

1. `GET …/chapters/{ch}/partition/preview?target_episode_s={目标时长}` 试算
   （片段不可分；plot 边界优先断集）→ checkpoint 确认 →
   `POST …/partition/commit` 落快照 → `GET …/episodes` 读集清单（`ep01…`）。
2. 产物 URL 一律经 deer-flow 代理交付：MGW 返回的媒体路径改写为
   `/api/media-gateway/file/<mgw 原样路径>`（
   `deerflow.media_gateway.providers.proxy_media_url` 同款约定），浏览器
   `<img>/<video>` 无需 Authorization 头。
3. 向用户汇报：每集时长 / clip 清单 / 逐镜产物代理 URL / QC 结论与迭代
   轮数摘要。

## 断点续作（中断 / 换会话）

- **数据全部在 MGW**：重启后从 MGW 重读结构
  （`GET /v1/drama` → chapters → storylines/plots/clips/shots）+ 各 clip 的
  `state`/`archives`，**不重建、不重复生成已过镜**。
- sidecar 文件（`/mnt/user-data/workspace/{drama-name}.progress.json`）只存
  游标：`{drama_id, chapter_ids, 当前 clip_id, 各 clip 迭代轮数, 模式,
  needs_human 清单}`。**不存**任何剧本/分镜设计数据（那是 MGW 的真源）。
- 恢复顺序：读 sidecar 游标 → MGW 核对结构仍在 → 从第一个非 `qc_pass` 的
  clip 继续阶段 3。

## 边界与不做的事

- 不在 deer-flow 建短剧/分镜/资产领域表（无 schema 改动）；管理类操作
  （批量整理/换装/种子库管理）引导用户走 MGW 控制台（skill
  `media-console-link`）。
- 不本地拼装合成 prompt——`prompt_preview` 的 `prompt` 是唯一合成出口。
- 不启停引擎模型、不碰引擎生命周期端点。
- 单剧流程可在 lead agent 直接跑；多章大批量可先确认总体计划，再按章
  委派 subagent 分段执行（令牌与幂等键随章隔离）。
