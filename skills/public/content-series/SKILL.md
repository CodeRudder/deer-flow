---
name: content-series
description: Use this skill when the user wants 内容创作矩阵 / 系列内容批量生产 — e.g. gives 一个想法或一篇现有文章/图片/视频素材 and asks to 产出一组不同格式的内容（文章/图文帖/组图/视频片段/短剧）with 统一风格. Top-level orchestration of the Media Gateway content pipelines — 输入入库 → 内容矩阵规划 → 逐选题路由到既有 skill 管线 → 完成度追踪 → 续作.
---

# 内容创作矩阵（content-series）

## 概述

本 skill 把**一个想法或一份现有作品**变成**一系列多格式的高质量内容**：
短篇文章、文生组图、图文帖子、视频片段、短剧——全部共享同一份风格指南与
视觉资产（系列一致性），逐选题路由到既有 skill 的底层编排模块，不重复实现
任何生成逻辑：

| 矩阵项格式 | 路由到 | 底层模块 |
|---|---|---|
| `article`（短篇文章） | LLM 直出（文本不落 MGW） | 本 skill 内完成 |
| `image_series`（文生组图） | `t2i-series` | `media_gateway/t2i_series.py` |
| `image_post`（图文帖子） | `image-text-post` | `media_gateway/image_text_post.py` |
| `video`（视频片段） | `video-clips` | `media_gateway/video_clips.py` |
| `drama`（短剧） | drama 结构 API 最小链（完整流程由 `drama-production` 承担） | `media_gateway/qc.py` |

顶层编排逻辑（输入适配 → 规划 → 受控并发生产 → 完成度追踪 → 续作）在
`media_gateway/content_series.py` 模块
（`deerflow.media_gateway.content_series`），本 skill 按下述流程驱动它。
矩阵与状态经 MGW 落库（`csplan_…` 登记 + 逐项状态回写），deer-flow 不建
内容领域表；会话侧只保留续作游标 sidecar（见文末）。

全程使用当前用户在 MGW 的 `h3t_` 令牌，用户之间数据完全隔离。

## 输入

- **想法**（必填其一）：一句话/一段话，直接进规划。
- **现有文章**（可选）：文本随计划携带（MGW 素材库上传契约只收
  images/video，文本不入库），规划器（你）直接阅读后派生选题。
- **图片/视频素材**（可选）：本地文件 → 先上传入库（`POST /v1/media`，
  `ingest_inputs`）→ 媒体 ID 供 video / image_post 管线引用。
- **规模**：选题数（缺省 3，验收下限）、每选题格式组合（缺省文章+组图，
  首选题加图文帖）。
- **模式**：`checkpoint`（缺省，矩阵需用户确认）/ `auto`（直接开产）。

## 流程（对应 `content_series.py` 中的函数）

### ① 输入适配（`ingest_inputs`）

- 想法 → 直接进 ②；
- 现有文章 → 文本随计划携带（不入库）；
- 图片/视频素材 → `POST /v1/media` 入库，收集 `media_ids`。

### ② 内容矩阵规划（`default_series_plan` + 你逐项覆写）

产出 :class:`SeriesPlan`：**垂类定位**（教程/科普/生活/运动…）+
**风格指南** + **选题清单**（每选题一个格式组合标记）。骨架是确定性的，
你（LLM）在此基础上逐选题覆写标题/简述/格式组合——格式必须落在
`article / image_series / image_post / video / drama` 内。

**风格指南**（`style_guide`，落 MGW）至少包含：

| 字段 | 写什么 |
|---|---|
| `visual_style` | 视觉风格（统一画风/色调/光线），合成后注入**每条管线** |
| `tone` | 口吻（合成进风格段，文章/文案/台词共享） |
| `banned_words` | 禁用词（不进生成提示词，作为你的写作/设计层负向约束） |

- **checkpoint 模式**：把矩阵（选题 × 格式 × 产物形态）展示给用户增删
  （`on_plan` 接缝），确认后才进 ④；改的是计划，不重算已确认项。
- **auto 模式**：用户明确说"直接做"时跳过确认。

### ③ 资产建立（视觉一致性的一次性投入）

开工前先建好反复引用的资产：封面模板/品牌元素/出镜角色卡（`POST /v1/media`
或托盘引用，`med_…`）。系列内所有选题共享这些资产——批量生产时只需逐选题
内容创作，不重设风格。

### ④ 逐选题生产（`ContentSeriesOrchestrator.run`，受控并发）

选题级并行（信号量可配，缺省 2）；每条管线自带 QC 闭环（t2i/image-text-post
内置，video 走设计层门控，drama 复用 §7.3 检验修正闭环）：

- 文章 → 你直出（正文须含统一风格段，可用注入的 `article_fn`）；
- 组图 → `run_t2i_series`（`style_desc` = 风格指南合成段）；
- 图文帖 → 既有 job/media 引用优先，无图则先出最小组图再 `run_image_text_post`；
- 视频 → `run_video_clips`（素材 `media_id`/文件 + 五要素分镜，风格随
  `notes` 落分镜方案；**缺素材直接标 `needs_human`，不硬造**）；
- 短剧 → 最小链（建剧 → 托盘 → 单集分镜 → 生成 → QC）；完整多集制作
  明确交给 `drama-production` skill。

### ⑤ 完成度追踪与交付

矩阵项状态机：`pending → running → done / failed / needs_human`。状态逐项
回写 MGW（`PATCH /v1/series/plans/{id}`），报告给出汇总：

- `done` 清单 + 各管线产物（代理 URL / 文本 / drama 结构 ID）；
- `needs_human` 清单 + 原因（缺素材 / 限次迭代仍未过 QC / 设计门失败）——
  **失败项按限次策略停在对应状态，显式列出，绝不静默丢弃**；
- `passed` = 全部 done，否则指明剩余项与建议动作（补素材重跑 / 人工处理）。

### ⑥ 续作（`SeriesCursor`）

sidecar 游标只记"哪些矩阵项已完成"（`series_id` + done 清单）。中断/增补
后重跑 `run(resume=cursor)`：已完成项旁路（不重复生产、不重复计费），新选题
或 `needs_human` 项按 ④ 继续走。设计数据一律从 MGW 重读，游标不做真源。

## 系列一致性（质量的关键）

1. **一份风格指南注入全部管线**：`compose_style_desc` 把视觉风格+口吻合成
   一段文本，组图的 `style_desc`、视频分镜 `notes`、短剧提示词、文章正文
   共享同一段——不要为单条管线单独编风格；
2. **视觉资产一次建立反复引用**（③）；
3. **禁用词是负向约束**：写文案/分镜/台词时自查，不依赖生成端拦截。

## 中断续作

- 游标 sidecar：`{"series_id": "csplan_…", "done": {"t01:article": …}}`
  （`SeriesCursor.to_json()`）；会话目录持久化，重跑时 `from_json` 载入；
- 矩阵真相在 MGW：`GET /v1/series/plans/{series_id}` 可查全部选题与状态；
- 增补选题 = 改 plan（新选题 id 不与已 done 项重复）→ 带 cursor 续跑。
