---
name: t2i-series
description: Use this skill when the user wants 文生组图 / 主题系列配图 / 多张系列图 — e.g. 给主题生成一组风格统一的图（月历、封面、配图组、亮/暗双版本等）。凡提到 千问图片模型 / qwen-image / moss-image / media gateway / 媒体网关 的文生图/组图请求，一律用本 skill（经 media_gateway 网关模型，无需任何图片 API Key，不要用需要 QWEN_IMAGE_API_KEY 的直连 qwen_image provider）。Orchestrates the Media Gateway 文生组图 pipeline — 组图规划（张数/每张画面/风格约束）→ 默认视为已授权（消息本身即生成指令，不再插入"回复确认"暂停）→ 逐张提交生成 → 一致性/QC 闭环 → 组图交付.
---

# 文生组图 / 主题系列配图（t2i-series）

## 执行方法（必读）

**生成组图只跑一条命令**（前台同步执行，脚本内部完成提交/轮询/下载）：

```bash
python /mnt/skills/public/t2i-series/scripts/generate_series.py \
  --theme "城市黄昏系列" --count 2 \
  --style "统一插画风，低饱和暖色调" \
  --out-dir /mnt/user-data/outputs/t2i-series/city-dusk
```

- 凭证由平台注入沙箱 env（`MEDIA_GATEWAY_BASE_URL` / `MEDIA_GATEWAY_TOKEN` /
  `MEDIA_GATEWAY_IMAGE_MODEL`），脚本自动使用当前用户凭证——**禁止**自行置备
  账号或读取/复用其他用户的令牌。
- 常用参数：`--count`（1–8，缺省 4）、`--style`（统一风格段）、`--mode`
  （缺省 `$MEDIA_GATEWAY_IMAGE_MODEL`）、`--aspect-ratio`（缺省 16:9）、
  `--max-attempts`（每张重试上限，缺省 3）、`--timeout`（每张轮询总超时，
  缺省 480s；qwen-image-std 实测约 40–60s/张）、`--out-dir`（缺省
  `/mnt/user-data/outputs/t2i-series/<主题 slug>`）、`--dry-run`（只打印将
  提交的请求）。逐张定制画面时用 `--prompt-group "第1张画面…" "第2张画面…" …`
  代替 `--theme/--count`。
- 产物写入 out-dir：`01.png` / `01.jpg`…（扩展名按产物字节自动判定）；
  每张进度打到 stderr，结束时 stdout 打印一份 JSON 摘要（`files[]`、
  `job_ids`、逐张状态）。

**严禁以下做法**（都会导致作业丢失或回合停滞）：

- ❌ 在沙箱里手写驱动脚本/自行调 MGW HTTP 接口；
- ❌ `nohup`、`&`、tmux 等任何后台化——媒体网关作业是异步的（提交 202 →
  轮询 `GET /v1/images/jobs/{id}` 到终态 → 从 `data[].url` 取产物），脚本已
  在前台封装全部轮询与下载，**不要自己实现轮询**，等命令跑完即可；
- ❌ 自行置备 MGW 账号/申请令牌。

生成完成后，用 `present_files` 把 out-dir 中的图片交付给用户（见文末「交付」）。

## 概述

本 skill 把**一个主题**变成**一组风格/主体一致的图**：每张图一单"文生图"
（同一 mode，逐张提交），由媒体网关（MGW）生成。沙箱内的实际执行入口是
`scripts/generate_series.py`（见「执行方法」，一条命令完成全组）；后端侧的
编排逻辑（可测试的调用序列）在 `media_gateway/t2i_series.py` 模块
（`deerflow.media_gateway.t2i_series`）。组图账本与作业状态全部落 MGW
（`ser_…` 登记 + jobs 状态机）。

全程使用当前用户在 MGW 的 `h3t_` 令牌（经后端置备体系获取），用户之间数据
完全隔离；不做任何"指定用户/令牌"操作。已有参考图/素材要先"改图/垫图"
时用 `image-generation` / `image-editing`，本 skill 是**纯文字生组图**。

## 输入

- **主题/选题**：一句话主题（必填），可带补充要求（用途：公众号配图 /
  系列封面 / 小红书组图；情绪；画幅）。
- **组图张数**（缺省 4，1–8）、**风格指南**（画风/色调/光线，缺省用
  "统一插画风，柔和自然光，低饱和暖色调"）、档位 `mode`（缺省
  `h3-frame-std`；高质可用 `h3-frame-hq`——提示耗时与额度差异）、
  `aspect_ratio`（缺省 `16:9`）、种子（可选，复现用）。

## 流程（按序执行；①②③可由 `t2i_series.py` 对应函数完成）

### ① 组图规划（plan/design：`default_series_plan` + LLM 逐张覆写）

先出骨架（`default_series_plan`：张数、统一风格参数、逐张节拍），再由你
（LLM）逐张精细设计。plan 是一个 dict：

| 字段 | 写什么 |
|---|---|
| `theme` | 主题一句话（全组共享） |
| `style.mode` / `aspect_ratio` / `short_edge` | 组图级生成参数——**全组必须一致**（一致性校验基准） |
| `style.style_desc` | 统一风格段——会拼进**每张**的提示词开头 |
| `images[].index` | 1 起连续编号（1..N） |
| `images[].subject` | 该张画面：主体在做什么（叙事节拍：开篇→入场→高潮→收束） |
| `images[].scene` | 该张场景/构图/光线（与前后张承接） |
| `images[].notes` | 补充约束（可选） |

连贯性写进每张的 `scene`（同一世界观/光线方向/主体形象描述保持同样的
关键词），跨张一致性靠**同一 style_desc + 同一主体描述**，不靠抽卡碰。

### ② 设计层校验（默认直过，不设确认门）

- 组图规划是设计层检查（对齐 `check_plan` 语义）：主题/风格段/每张画面与
  场景齐备、张数 1–8。任何一项不过都不得提交生成——回显问题清单，补齐后再
  执行。
- **用户消息本身即生成指令，默认视为已授权，不插入"回复确认"暂停**；只有
  主题明显缺失（无法成图）时才先追问一次。用户明确给过张数/风格就照做，
  不要再重复确认。

### ③ 逐张提交生成（`generate_series.py` 一条命令）

执行入口就是「执行方法」小节的那条命令，脚本逐张 `POST /v1/images/generations`：
全组同一 `mode`/比例，每张一个独立 `Idempotency-Key`（含张号+尝试序——同参数
重发返回同一作业不重复消耗；重试换 key 天然不会撞 409）；随后前台轮询
`GET /v1/images/jobs/{id}` 到终态，从 `data[].url` 取产物字节写入 out-dir。
单张失败自动按 `--max-attempts` 重试。

### ④ QC 闭环（每张 ≤3 轮：归因 → 修正 → 重出）

> **单轮模式**：简单生成任务默认"单轮生成、不做 QC 重试"（脚本已内建基本校验），与"一条命令"契约保持一致；仅当用户明确要求质量闭环时才启用多轮 QC。

- 作业 `failed`：先归因——失败信息带 seed/伪影/抖动特征 → **模型能力边界/
  随机性**（`seed` 归因：换种子重抽，不改设计）；其余按 **prompt 缺陷**兜底
  （改写该张提示词后重出，可注入 `fix_fn` 承接你的改写）。
- 每张自动迭代 ≤3 轮；仍失败标记 `needs_human` 并**继续其余张**（auto 语义，
  失败项不静默丢失，报告里逐张列出）。
- 生成后可让用户目检；不满意的张回到 ① 只改那张的 `subject`/`scene`
  （改设计 ≠ 重新生成全组），把改后画面写进 `--prompt-group` 单独重跑那张
  即可（同样一条命令，不要手写脚本）。

### ⑤ 一致性校验与交付（`check_consistency` + series 登记）

- **一致性校验**：逐张核对 mode/比例/短边与组图风格一致、提示词含统一风格
  段、张数齐全（每张都有产物）——有问题在报告里拦截说明。
- 全组完成后登记进 MGW（`POST /v1/images/series` → `ser_…`），批状态在
  MGW 可查；deer-flow 侧无内容领域表。
- 交付：用 `present_files` 交付 out-dir 中的图片文件，并逐张列出 张号 /
  状态 / 文件名 / 迭代轮次（脚本的 JSON 摘要里有现成的 `files[]`）。

## 中断续作

- MGW 是数据真源：作业与组图账本都在 MGW；会话侧 sidecar 只存断点游标
  （theme / plan / 各张 job_id / 已完成张号），随会话输出目录保存
  （`outputs/{name}.t2i-series.json`）。
- 会话中断后重启：读 sidecar → 已完成的张直接取产物（按 job_id 轮询），
  未完成的张按原 plan 重新提交（幂等键含张号+尝试序，同参数不会重复排队），
  **不重复生成已交付张**。

## 未覆盖的管理操作（引导控制台跳转）

以下操作本 skill 不实现，引导用户打开媒体控制台（用 `media-console-link`
skill 取跳转链接）：组图批量重命名/归档、作业历史清理、档位与额度配置。

话术示例：「这个操作需要在媒体控制台完成，我给你一张免登链接 →（调
media-console-link）」。

## 错误处理

| 现象 | 处理 |
|---|---|
| `429 queue_full` / 限流 | 透传 Retry-After，稍后重试，不做本地重试风暴 |
| `409 conflict` | 同 Idempotency-Key 不同参数；换 key 或复用原参数 |
| 作业 `failed` | 走 ④ 的归因修正闭环 |
| 一致性校验不过 | 回 ① 修 plan（风格参数/缺张），不交付半套不一致的组图 |
