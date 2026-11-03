---
name: media-gateway-api
description: Use this skill when the agent needs to 直调媒体网关 / media gateway (MGW) API 生图、生视频或组图 — e.g. 网关生图 / 网关生视频 / 无 API Key 生图 / 没配原厂 Key 要走自托管网关 / 要用网关专属档位（h3-frame-*、h3-clip-*、h3-i2i-*、h3-ref-*、qwen-image、moss-image、wan、ltx 等）/ 直接提交与轮询 /v1/images/generations 作业。覆盖异步作业语义（202 → 轮询 → data[]）、幂等键、产物读取与错误重试。不改变也不替代原有原厂直连 provider（openai_image / qwen_image / minimax / openai 兼容等照常可用）。
---

# 媒体网关（Media Gateway / MGW）API 直调

> **契约权威来源**：端点契约以本目录 `openapi.json` 快照为准（网关 `/openapi.json`
> 实时版可能更新，跑 `scripts/sync_mgw_openapi.py` 刷新快照与
> `endpoints-summary.md`；`--diff` 可一键核对部署后有无变更）。本文件为语义速查，
> 字段/路径与快照冲突时以快照为准。

## 1. 与原厂 API 的关系（先读这个）

**原厂直连 provider 完全不受影响。** `openai_image` / `qwen_image` / `minimax` /
`openai` 兼容等既有 provider 及其配置（API Key、base URL、默认模型）原样可用，
既有 `image-generation` / `video-generation` / `image-editing` skills 与工作流
零改动——它们走的是原厂 API，本 skill 描述的 MGW 通道是**并存的新通道**，两者：

- **凭模型名/档位名区分**：原厂模型名（如 `doubao-*`、`qwen-*` 经原厂 Key）
  走原厂 provider；MGW 档位名（`moss` / `qwen-image` / `wan` / `ltx` /
  `h3-frame-*` / `h3-clip-*` / `h3-i2i-*` / `h3-ref-*`）走 `media_gateway`
  provider 或本节的直调端点。**不互通、不改写**——不要把原厂模型名塞进网关
  请求，也不要把网关档位名塞进原厂 provider。
- **MGW 什么时候用**：①用户没配对应原厂 API Key；②明确要走自托管网关；
  ③要用网关专属档位（见 `GET /v1/images/modes` 能力发现）。
- **原厂什么时候照旧**：已配置对应 Key 且需要其独有参数/模型时，继续用既有
  provider 与 skills，不要绕道 MGW。

## 2. 凭证（零配置使用）

沙箱会话由 `MediaGatewayCredentialMiddleware`（后端 5b 中间件）自动注入**当前
用户身份**的凭证（per-user 隔离，JIT 置备的 `h3t_` 令牌），脚本直接读环境变量：

| 环境变量 | 含义 |
|---|---|
| `MEDIA_GATEWAY_BASE_URL` | 网关 API 基址（必填） |
| `MEDIA_GATEWAY_TOKEN` | 当前用户 Bearer 令牌（必填，勿打日志、勿外传） |
| `MEDIA_GATEWAY_IMAGE_MODEL` | 缺省生图档位（请求未给 `mode` 时用） |
| `MEDIA_GATEWAY_VIDEO_MODEL` | 缺省生视频档位 |
| `MEDIA_GATEWAY_CONSOLE_URL` | 网关控制台地址（用户要看进度/管理作业时给跳转，见 `media-console-link` skill） |

**无需也不要**使用 `MEDIA_GATEWAY_ADMIN_TOKEN`——沙箱里它已被强制置空；admin
令牌仅平台内部置备用，日常生成请求用它会破坏 owner 隔离。MGW 未配置时中间件
零注入（env 变量不存在），此时走原厂 provider 即可。

## 3. API 速查

统一约定：`$BASE` = `$MEDIA_GATEWAY_BASE_URL`，`$TOKEN` =
`$MEDIA_GATEWAY_TOKEN`，鉴权头 `Authorization: Bearer $TOKEN`。错误信封统一为
`{"error":{"code":"…","message":"…","retryable":false}}`。

```bash
AUTH="Authorization: Bearer $MEDIA_GATEWAY_TOKEN"

# 能力发现：档位列表（name/available/quality/params/eta_s/needs_reference/engine_task/description）
curl -s "$MEDIA_GATEWAY_BASE_URL/v1/images/modes" -H "$AUTH"

# 提交生图/生视频：缺省异步 → HTTP 202 + 作业简表 {"id":"img_…","status":"queued","eta_s":…}
curl -s -X POST "$MEDIA_GATEWAY_BASE_URL/v1/images/generations" \
  -H "$AUTH" -H 'Content-Type: application/json' -H 'Idempotency-Key: my-task-001' \
  -d '{"mode":"h3-frame-std","prompt":"…","aspect_ratio":"16:9","count":1}'
#   · 视频档位（或要 mp4 产物）加 "output":"video" → data[].url 指向 kind=video
#   · 可选 "wait":true → 阻塞到终态（HTTP 200 + 完整作业体，completed 时含 data[]）
#   · Idempotency-Key：同 key 同请求体 → 返回同一笔作业不重复排队；同 key 异请求体 → 409

# 轮询作业：status ∈ queued/running/completed/failed/cancelled
curl -s "$MEDIA_GATEWAY_BASE_URL/v1/images/jobs/img_…" -H "$AUTH"
#   completed 时 data[] 每项 {"url","v","meta"}；url = /v1/images/jobs/{id}/file?kind=image&variant=N&v=…

# 产物字节（唯一端点；kind=image|thumb|video|frame|ref|ref_original|ref_thumb）
curl -s "$MEDIA_GATEWAY_BASE_URL/v1/images/jobs/img_…/file?kind=image&variant=0" \
  -H "$AUTH" -o out.jpg

# 取消/删除：queued → 取消；终态 → 删除；running 不带 force → 409 running_no_cancel
curl -s -X DELETE "$MEDIA_GATEWAY_BASE_URL/v1/images/jobs/img_…" -H "$AUTH"

# QC 评估（seed/similarity/quality 等）；variant 缺省 0
curl -s "$MEDIA_GATEWAY_BASE_URL/v1/images/jobs/img_…/assessment" -H "$AUTH"

# 多档对比（modes 1–4 个，须同 engine_task 族；同族限制跨族 400）
curl -s -X POST "$MEDIA_GATEWAY_BASE_URL/v1/images/compare" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"modes":["h3-frame-draft","h3-frame-hq"],"prompt":"…"}'

# 全局作业列表（只读；scope=gateway|engine|all，owner=me 等，详见网关 jobs.md）
curl -s "$MEDIA_GATEWAY_BASE_URL/v1/jobs?scope=gateway&owner=me&limit=50" -H "$AUTH"
```

**异步作业语义（核心）**：`POST /v1/images/generations` 缺省立即 **202**（作业
入网关队列，引擎不可达也不拒收，唯一拒绝理由是队列满 → 429 `queue_full`）→
轮询 `GET /v1/images/jobs/{id}` 到终态 → completed 后从 `data[].url` 取产物字节。
判「卡死」别只看 `status`/`progress`，看 `progress_estimate` 与 `engine_job_ids`。

**浏览器场景坑**：`<img src>` / 顶层导航带不了 Authorization 头，直接挂产物
URL 会 401。前端走 deer-flow 代理 `/api/media-gateway/file/...`（服务端带用户
令牌转发），或用 `fetch` + Bearer 头转 objectURL。

## 3b. 换装（dressup）端点速查

换装试穿的完整流程编排见 `dressup-tryon` skill（脚本一条命令封装）；这里只列
端点，供直调/排障用。台账 CRUD 同步；生成/定妆照异步（作业轮询同 §3 的
`/v1/images/jobs` 语义）；qc 同步（VLM）；owner 隔离（跨 owner 一律 404）。

```bash
# 建模特（201 即回；自动起定妆照作业，portrait_status 异步回填 done + portrait_url）
curl -s -X POST "$MEDIA_GATEWAY_BASE_URL/v1/dressup/models" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"模特A","desc":"22岁·高挑·鹅蛋脸·黑长直","source_media_id":"med_…"}'
# 台账查重（复用优先——定妆照一次约 50s，重复模特不要重建）
curl -s "$MEDIA_GATEWAY_BASE_URL/v1/dressup/models" -H "$AUTH"

# 建服装（或 /garments/from_media 一键导入，自动 VLM 属性分析出 keywords）
curl -s -X POST "$MEDIA_GATEWAY_BASE_URL/v1/dressup/garments" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"碎花裙","gtype":"onmodel","media_id":"med_…","keywords":["白底碎花","吊带长裙"]}'

# 建试穿（同步台账）→ 提交生成（异步 job_id）→ 轮询 tryon 详情回填 status/outputs
curl -s -X POST "$MEDIA_GATEWAY_BASE_URL/v1/dressup/tryons" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"model_id":"dm_…","garment_id":"dg_…"}'
curl -s -X POST "$MEDIA_GATEWAY_BASE_URL/v1/dressup/tryons/dt_…/generate" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"output":"image","pose":"front","aspect_ratio":"3:4"}'
curl -s "$MEDIA_GATEWAY_BASE_URL/v1/dressup/tryons/dt_…" -H "$AUTH"
#   · pose 预设：front/side/back/stool/walking/bag（认中文别名）；白名单外必须给 "pose_text"
#   · scene_id 与 "scene_source":"garment" 互斥；garment_frames 可手选服装参考图 1–3 张

# VLM 衣着保真质检（同步）；FAIL 给 qc.suggest → 按建议词自动重出（retry ≤3 次，超限 400）
curl -s -X POST "$MEDIA_GATEWAY_BASE_URL/v1/dressup/tryons/dt_…/qc" -H "$AUTH"
curl -s -X POST "$MEDIA_GATEWAY_BASE_URL/v1/dressup/tryons/dt_…/retry" -H "$AUTH"
```

注意：建档 body `extra=forbid`（多余字段 400）；VLM×渲染互斥走 `resource_gate`，
冲突返回 503 + `Retry-After`（按 §5 语义等待重试）；产物 URL 指向
`/v1/images/jobs/{id}/file`，浏览器渲染走 §3 的 `/api/media-gateway/file/...` 代理。

## 4. 推荐用法

- **组图（多张系列图）**：优先用封装脚本
  `/mnt/skills/public/t2i-series/scripts/generate_series.py`（进程内封装提交 →
  轮询 → 下载，配合 `t2i-series` skill 的规划/QC/一致性闭环）。**不要**在沙箱
  里另起后台进程或手写驱动脚本重复这套异步逻辑。
- **单张 / 单段视频**：可按 §3 直调端点；或走既有 `image-generation` /
  `video-generation` skills（`media_gateway` provider 已注册进 registry，模型
  可选中立，async create→poll→download 已封装）。
- **封装已覆盖异步语义（202 → 轮询 → data[]）时不要自己造轮子**——重复实现
  幂等键/轮询/重试只会引入新 bug。

## 5. 错误与重试

| HTTP | `error.code` | 处理 |
|---|---|---|
| 400 | `invalid_request` | 参数类（未知 mode、参考图↔档位不一致、画布超预算等）；改请求，不要盲目重试 |
| 401 | `unauthorized` | 令牌缺失/失效——**平台会自动轮换 per-user 令牌，勿自行处理 admin 令牌**；重试前确认用的是 env 注入的 `MEDIA_GATEWAY_TOKEN` |
| 404 | `not_found` | 作业/产物不存在或不可见（越权与不存在同为 404） |
| 409 | `conflict` | 同 Idempotency-Key 异请求体；换 key 或对齐请求体 |
| 409 | `running_no_cancel` | DELETE running 作业未带 `?force=1`（force 是逃生口不是常规操作） |
| 429 | `queue_full` | 排队满，可重试；**遵守 `Retry-After` 头** |
| 502 | `engine_error` | 引擎侧失败；`retryable=true` 时可换种子/键重试 |
| 503 | `engine_unavailable` | 上游不可达（一问一答路径）；带 `Retry-After`，等待后重试 |

通用：错误体里的 `retryable` 是重试判据；重试提交时**沿用同一 Idempotency-Key**
（同 key 同请求体返回同一作业，天然防重复消耗），但若 409 conflict 说明请求体
变了，应换新 key。

## 6. 原厂 API 何时仍应使用

- 用户/部署**已配置对应原厂 Key**（OpenAI/MiniMax/Qwen 等）且需要其独有参数、
  独有模型版本或原厂生态能力时——照旧走原厂 provider 与既有 skills。
- MGW 未配置（env 无注入）时，MGW 通道不可用，原厂 provider 是唯一路径。
- 两者可以并存：同一部署里部分模型走原厂、部分走网关，按模型名/档位名路由，
  互不影响。
