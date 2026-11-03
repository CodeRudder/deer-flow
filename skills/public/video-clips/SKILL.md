---
name: video-clips
description: Use this skill when the user wants 组图生视频 / 分镜片段视频 — e.g. gives a set of reference images (素材/组图/分镜图) and asks to 生成分镜视频 / 让图片动起来 / 组图转视频. Orchestrates the Media Gateway groupvid pipeline — 素材入库 → 分镜方案（plan/design，每镜五要素）→ 设计图目检 → 逐镜提交生成 → QC → 逐镜产物交付.
---

# 组图生视频 / 分镜片段（video-clips）

## 概述

本 skill 把**一组参考图**变成**分镜片段视频**：每个分镜一单"参考图生视频"
（ref2va），由媒体网关（MGW）逐镜生成。编排逻辑（可测试的调用序列）在
`media_gateway/video_clips.py` 模块（`deerflow.media_gateway.video_clips`），
本 skill 按下述流程驱动它；分镜/方案数据全部落 MGW，会话侧只保留断点游标
sidecar（见文末"中断续作"）。

全程使用当前用户在 MGW 的 `h3t_` 令牌（经后端置备体系获取），用户之间数据
完全隔离；不做任何"指定用户/令牌"操作。

## 输入

- **参考图/素材**：用户给的图片文件（上传到会话或给出 URL 抓取结果），或
  素材库中已有的组图媒体（`med_…`）。一张也可以，多张（1–20）更好——
  分镜从组图中选参考图。
- 分镜数量（缺省 3，1–10）、每镜时长档（1/5/10/15 秒，缺省 5）、补充要求
  （氛围/动作偏好）。

## 流程（按序执行；①②③可由 `video_clips.py` 对应函数完成）

### ① 素材入库（`ingest_materials`）

把用户素材以 `kind=images` 上传进 MGW 素材库（`POST /v1/media`）。若用户
给的是已有 `med_…`，跳过入库直接引用。入库后先做组图分析
（`ensure_analysis`，幂等）。

### ② plan/design 分镜方案（`draft_storyboard` + `apply_shot_designs`）

1. 先经 MGW 出初稿（`POST /v1/media/{id}/storyboard`：参考图分配 + 基础动作）；
2. 再由你（LLM）逐镜精细设计**五要素**，经
   `PATCH /v1/media/{id}/storyboard/shots/{index}` 落库（`build_shot_patch`
   负责映射）：

| 要素 | 写什么 | 落到 |
|---|---|---|
| 动作 | 从哪开始→过程→结束，一句话完整动作链 | `action` 主干 |
| 表情 | 情绪标签 + 微表情（与对话情绪对齐） | 追加进 `action`（"表情：…"） |
| 对话 | 台词，含语气/停顿标注 | 追加进 `action`（"台词：…"） |
| 场景 | 机位/构图/光线/镜头语言 | `camera` |
| 节奏 | 时长档（只认 1/5/10/15）与前后镜承接 | `duration_tier` |

参考图编号（`refs`）是组图内 **1-based 全组编号**，第一参考图是人物锚——
跨镜人物一致性靠"每镜 refs 都含同一张人物图"。

### ③ 设计层 QC（零 GPU，提交前必过：`qc_check_storyboard`）

逐镜检查：五要素齐备、refs 非空且不越界、时长档合法。**任何一项不过都
不得提交生成**——回显问题清单，自动补齐设计后重验。

### ④ 用户确认（checkpoint）/直过（auto）

- **checkpoint 模式（缺省）**：把分镜板（镜号/参考图/五要素/时长）整理成
  表格给用户目检；用户也可要求先出**分镜设计图**（t2g 文生分镜，低消耗
  先行目检——提示用户该步骤耗生图额度）。确认后才进 ⑤。
- **auto 模式**：用户明确说"直接生成/自动"时跳过确认，仍执行 ③ 的 QC 门控。

### ⑤ 逐镜提交生成（`submit_generation`）

`POST /v1/media/{id}/generate`，每镜一单 ref2va 作业，自动带
`Idempotency-Key`（同参数重发返回同一批作业，不重复消耗）。可传
`shots` 子集（只重做失败镜）、`seed`、`candidates`（1–3 候选）。
提交后用 `await_artifacts` 轮询各镜作业到终态。

### ⑥ 生成层 QC 与交付

- **每镜产物必须落盘交付（强制）**：作业到 `completed` 后，用沙箱内 MGW
  凭据把视频字节下载进 outputs（curl
  `$MEDIA_GATEWAY_BASE_URL/v1/images/jobs/{job_id}/file?kind=video&variant=N`
  带 `$MEDIA_GATEWAY_TOKEN`），存为
  `/mnt/user-data/outputs/<素材名>/shot{镜号}-cand{候选}.mp4`，然后调用
  `present_files` 交付——**只给文本 URL 不算交付**，前端视频卡片与产物
  收集都依赖 outputs 落盘。
- 每镜产物同时经 deer-flow 代理 URL 交付（`/api/media-gateway/file/…`，
  免令牌直下），逐镜以 Markdown 链接列出（不要放代码块里）：镜号 / 时长 /
  `job_id` / 产物链接 / 状态。链接写相对代理路径
  `[shot1.mp4](/api/media-gateway/file/…)`——用户浏览器与 deer-flow 同源，
  带会话 cookie 即可直接播放；**不要**用 `$MEDIA_GATEWAY_CONSOLE_URL` 拼
  产物链接：那是 MGW 控制台外部地址（后端 `settings.display_console_url()`），
  MGW 不提供 `/api/media-gateway/file` 代理路由（实测 404），只用于
  `media-console-link` 跳转。作业未到终态或失败必须原样报出
  `job_id` + 状态 + 错误，不得只写"已完成"。
- 失败镜：先归因（提示词缺陷 → 回 ② 改设计；参考图错 → 改 refs；模型能力
  边界 → 降级时长档或换 seed 重抽），修正后只对失败镜重新提交（`shots` 子集），
  每镜自动迭代 ≤3 轮；仍失败则 checkpoint 模式回交用户，auto 模式标记后
  继续其余镜头。
- 生成后可让用户目检；用户不满意的镜回 ② 改五要素重新生成（改设计 ≠ 重新
  上传，数据仍在 MGW）。

## 中断续作

- MGW 是数据真源：媒体、分镜方案、提交记录都在 MGW；会话侧 sidecar 只存
  断点游标（`SidecarCursor`：media_id / stage / 当前镜 / job_ids），随会话
  输出目录保存（`outputs/{name}.video-clips.json`）。
- 会话中断后重启：读 sidecar 游标 → `read_storyboard` 从 MGW 重读现行方案 →
  按游标阶段继续（未提交的镜继续设计/确认；已提交的镜按 job_ids 轮询取产物，
  **不重复生成已过镜**）。

## 未覆盖的管理操作（引导控制台跳转）

以下操作本 skill 不实现，引导用户打开媒体控制台（用 `media-console-link`
skill 取跳转链接）：

- **克隆组图**（已生成过视频的组图复制副本再设计）；
- **批量素材整理**（批量重命名/分类/删除素材库媒体）；
- **换装 / 形象帧设定 / 锚定资产生成**。

话术示例：「这个操作需要在媒体控制台完成，我给你一张免登链接 →（调
media-console-link）」。

## 错误处理

| 现象 | 处理 |
|---|---|
| `400 还没有组图分析结果` | 先跑 `ensure_analysis` 再设计分镜 |
| `400 kind≠images` | 素材不是组图（是视频）；改用抽帧入库或换素材 |
| `404 media 不存在` | media_id 错或非本人素材；让用户确认素材来源 |
| `409 conflict` | 同 Idempotency-Key 不同参数；换 key 或复用原参数 |
| 作业 `failed` | 走 ⑥ 的归因修正闭环 |
