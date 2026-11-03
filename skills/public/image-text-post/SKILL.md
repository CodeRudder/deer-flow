---
name: image-text-post
description: Use this skill when the user wants 图文帖子 / 组图文案 / 社媒图文 — e.g. gives a topic (选题) plus reference images or asks to generate them, and wants 标题/正文/图注 + 版式组装成可发布的图文帖子. Orchestrates the Media Gateway image-text-post pipeline — 组图来源（新生成/已有素材入库）→ LLM 文案（与图内容对齐）→ 版式组装 → VLM 抽检（图文相符）→ 交付 markdown 卡片.
---

# 图文帖子（image-text-post）

## 概述

本 skill 把**一个选题 + 一组图**组装成**可发布的图文帖子**：图可以来自媒体
网关（MGW）的既有生成产物、素材库已有组图，或用户上传的素材（先入库）；
文案（标题/正文/逐图图注）由你（LLM）生成并与图内容**对齐**；组装成结构化
post 对象后经 **VLM 抽检**核对图文相符，不符则带理由重写文案。编排逻辑
（可测试的调用序列）在 `media_gateway/image_text_post.py` 模块
（`deerflow.media_gateway.image_text_post`），本 skill 按下述流程驱动它。

全程使用当前用户在 MGW 的 `h3t_` 令牌（经后端置备体系获取），用户之间数据
完全隔离；不做任何"指定用户/令牌"操作。版式是**数据模板**（post 对象），
不做前端渲染；交付格式为 markdown 卡片。

## 输入

- **选题**（topic）：一句话主题/角度，文案围绕它展开。缺省时先与用户确认。
- **图**（三选一，可混合）：
  - 既有生成产物：作业 ID（`imgjob_…`，status=completed）；
  - 素材库已有组图：媒体 ID（`med_…`，kind=images）；
  - 用户素材：本地图片文件 → 先上传入库（`POST /v1/media`，kind=images）。
- 补充要求：风格/口吻/字数偏好、目标渠道（影响文案语气）。

## 流程（按序执行；对应 `image_text_post.py` 中的函数）

### ① 组图来源解析（`resolve_images` / `_ingest_media`）

把每种来源统一解析为图片条目（`{mgw_path, proxy_url, position}`）：

- 作业产物 → `GET /v1/images/jobs/{id}` 校验已完成，路径
  `/v1/images/jobs/{id}/file?kind=image`；
- 素材库媒体 → `GET /v1/media/{id}` 校验 kind=images，路径
  `/v1/media/{id}/file?kind=original`；
- 本地文件 → `POST /v1/media` multipart 入库后按媒体引用（入库一次，可反复
  用于后续帖子，改文案 ≠ 重新上传）。

所有图在交付时改写为 deer-flow 代理 URL（`/api/media-gateway/file/…`，
免令牌直下）。组图至少一张，3–9 张阅读体验最佳。

### ② 文案生成与对齐（`llm_copy` / `validate_copy`）

由你（LLM）生成三件套，逐项满足对齐约束（`validate_copy` 硬校验，违规
不得进入组装）：

| 产物 | 约束 |
|---|---|
| 标题 | 非空，≤40 字；一句话点出选题价值 |
| 正文 | 非空；开头承接标题，结尾引导互动；不逐图复述 |
| 图注 | **一图一条**，数量与组图一致；每条 ≤60 字；描述该图实际画面（与图内容对齐，不虚构图中没有的元素） |

对齐是硬约束：图注必须来自图里能看到的内容 + 选题语义，拿不准的图先看图
（或标注"示意"）再写。

### ③ 版式组装（`build_post`）

组装结构化 post 对象（数据模板，不做前端渲染）：

```
{ topic, title, body,
  images: [ { position, source_kind, source_ref, url, caption } ],
  layout: { template: "image-text-card", caption_position: "below", title_position: "top" } }
```

`images[].url` 为代理 URL；`caption` 与图按下标对齐。

### ④ VLM 抽检（`make_vlm_check` / `run_image_text_post`）

把标题 + 逐图图注作为质检问题，连同组图发给 MGW 本地 VLM
（`POST /v1/vlm/chat`，`think=false`，vlm.md），要求返回
`{"match": bool, "reason": str}` JSON 结论：

- **相符** → 通过，交付；
- **不符** → 把 VLM 理由作为 feedback 回 ② 重写文案**一次**，复检；仍不符
  → 报告用户（列出每轮不符原因），不要静默放行；
- **MGW 未配置 VLM**（503 `engine_unavailable` / 未注入）→ 跳过抽检，
  交付时向用户注明"未经 VLM 图文核对"。

### ⑤ 交付 / 发布格式（`to_markdown_card`）

交付内容：post 对象（结构化数据）+ markdown 卡片（标题 → 正文 → 逐图
`![图注](代理URL)` + 斜体图注）。把 markdown 卡片直接展示给用户，post
对象供下游渠道适配使用。

## 未覆盖的发布渠道与管理操作（引导控制台跳转）

本 skill 只到"可发布的图文帖子"为止。**实际发布**到社媒渠道（公众号/小红书
/微博等）、以及素材库批量管理、品牌模板设置等操作不在本 skill 范围，引导
用户打开媒体控制台（用 `media-console-link` skill 取跳转链接）。

话术示例：「帖子已经组装好了（见上面的 markdown 卡片）。发布到具体渠道需要
在媒体控制台完成，我给你一张免登链接 →（调 media-console-link）」。

## 错误处理

| 现象 | 处理 |
|---|---|
| 组图来源为空 / 未知类型 | 与用户确认图的来源；至少需要一张图 |
| 作业未完成（status≠completed） | 先轮询到终态；failed 作业回生成侧归因修正 |
| 素材 kind≠images | 视频素材不能直接做组图；抽帧入库或换素材 |
| 文案对齐校验不过 | 按问题清单补齐/裁剪后重验，不得带病组装 |
| VLM 503 `rendering_busy` | 带 `Retry-After` 自动重试（与渲染互斥） |
| VLM 两轮抽检仍不符 | 回交用户：附每轮 reason，建议换图或人工改文案 |
