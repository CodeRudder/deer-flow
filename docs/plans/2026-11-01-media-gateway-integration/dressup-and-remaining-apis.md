# MGW 未对接 API 盘点与换装（dressup）对接方案

> 日期：2026-11-02 · 状态：调研定稿（未改任何代码）
> 信息源：网关仓库 `minimax-h3-gateway`（`gateway/h3img/` + `docs/integration/api/`）、
> 真实网关探活 `http://172.17.0.1:8000`（openapi.json，131+ 路由）、
> deer-flow 侧 `backend/packages/harness/deerflow/media_gateway/` 与 `skills/public/`。
> 原始设计口径见本目录 [design.md](design.md)：深度功能（短剧/换装/组图）原计划只给控制台
> 链接、不在 deer-flow 重建领域状态机。本方案在保留该原则的前提下，把**换装**升级为
> skill 直调（试点），其余深度功能维持控制台跳转。

---

## 1. 网关 API 全量盘点：已对接 vs 未对接

deer-flow 已对接（代码层 `media_gateway/` 各模块 + provider，或 skill 直调）：

| 能力 | 端点 | deer-flow 侧入口 |
|---|---|---|
| 能力发现 | `GET /v1/images/modes` | `providers.py`（MODES_PATH） |
| 生图/生视频作业 | `POST /v1/images/generations`、`GET /v1/images/jobs/{id}`、`DELETE …/jobs/{id}`、`GET …/file` | provider + skill `media-gateway-api` |
| 多档对比 | `POST /v1/images/compare` | `providers.py`（COMPARE_PATH） |
| QC 评估 | `GET /v1/images/jobs/{id}/assessment` | `qc.py` + skill |
| 形态锁定 | `GET/PUT/DELETE /v1/shape-lock` | `providers.py` |
| 素材库 | `POST/GET /v1/media`、`GET/DELETE /v1/media/{id}`、`/analyze`、`/analysis`、`PATCH /analysis/shots/{i}`、`/analysis/summary` | `image_text_post.py` / `t2i_series.py` / skill |
| 组图生视频 | `POST /v1/media/{id}/generate`、`GET`、`POST /storyboard`、`GET`、`PATCH /storyboard/shots/{i}`、`POST /prompt` | `video_clips.py` + skill `video-clips` |
| 内容系列 | （上述 media 系端点组合） | `content_series.py` + skill `content-series` |
| 短剧（基础） | `POST/GET /v1/drama`、`/chapters`、`/plots`、`/storylines` | skill `drama-production`（深度分镜/生成/QC 仍走控制台） |
| 身份贯通 | `POST /v1/auth/ticket`（控制台免登）、`POST /v1/users`、`POST /v1/users/{uid}/tokens`（JIT 置备） | `provisioner.py` + skill `media-console-link` |

**未对接**（按家族分组，方案见 §3）：

| 家族 | 端点组 | 备注 |
|---|---|---|
| **换装（dressup）** | `/v1/dressup/*` 全部 24 条：models/garments/scenes/tryons CRUD、from_media、portrait/*、attrs/regenerate、generate/qc/retry/video、batch、export | **本方案优先项**，已在真实网关验证跑通（§2） |
| 组图任务直调 | `POST /v1/tasks/groupvid`、`/v1/tasks/t2g`、`/v1/tasks/{id}/design|plan|submit`、`/v1/tasks/t2g/{id}/to-media` | deer-flow 目前只走 `media/{id}/generate` 编排路，tasks 直调是旧入口，暂不接 |
| 分镜规划 | `POST /v1/media/{id}/plan`、`/plan/estimate`、`/plan/board.md`、`/plan/submit`、`GET /plan` | deer-flow 只用了 storyboard；plan 是更重的分镜规划链 |
| 素材复用/克隆 | `POST /v1/media/{id}/replicate|clone`、`GET /replicate`、`/assets`、`POST /assets`、`/identity-frame`、`/frame-scan`、`/extract-frames`、`GET /file` | 视频转素材/角色一致性的底层件 |
| 规则库 | `/v1/rules/*`（extract/list/get/patch/storyboard） | 从参考片提取分镜规则，独立子系统 |
| 种子匹配 | `/v1/seedmatch/*`（13 条：library/traits/match/predict/score/refs…） | 独立研究型子系统（有独立 seedmatch-serve） |
| VLM 直调 | `GET /v1/vlm/models|model`、`POST /vlm/model`、`POST /v1/vlm/chat` | deer-flow 后端已注入各自 VLM；仅当要用网关本地 VLM 时才需要 |
| 运维 | `/v1/queue/summary`、`/v1/jobs`、`/v1/tags`、`/v1/models*`、`/v1/model-services/*`、`/health` | 平台运维，deer-flow 不需要 |
| 内嵌页面 | `/files/*`（图床）、`/summer/*`（字幕/配音）、`/drama/*` 页面 | 控制台/跳转范畴 |
| 网关内嵌 Agent | `/v1/agent/*`（sessions/messages/stream/steer…） | 与 deer-flow 本体定位冲突，不接 |

---

## 2. 换装 API 契约与真实网关验证

### 2.1 契约摘要

完整契约见网关 `docs/integration/api/dressup.md`（代码真源 `gateway/h3img/api.py`
dressup 段）。要点：

- **三库台账 → 试穿记录 → 生成 → 质检 → 视频 → 导出**：
  1. `POST /v1/dressup/models`（建模特，**自动触发定妆照**三视图拼图，201 即回，`portrait_status` 异步回填）
  2. `POST /v1/dressup/garments`（或 `/garments/from_media` 从素材库一键导入，自动 VLM 属性分析）；`/scenes` 可选第三输入
  3. `POST /v1/dressup/tryons` `{model_id, garment_id[, scene_id]}` —— 纯台账，同步 201
  4. `POST /v1/dressup/tryons/{id}/generate` —— 异步作业（返回 `job_id`）：
     - `output=image`（缺省，统一生图服务，画幅缺省 3:4）；`output=video`（ref2va 视频路，`action=walk|catwalk|turn`，`duration=1/5/10/15`）
     - `pose` 预设 `front/side/back/stool/walking/bag`（认中文别名）或 `pose_text`/`pose_media_id` 自定义
     - `garment_frames` 手选服装参考图 1–3 张；`scene_id` 与 `scene_source:"garment"` 互斥
  5. `GET /v1/dressup/tryons/{id}` —— 幂等回填 `status/outputs`（产物 URL 指向 `/v1/images/jobs/{id}/file`）
  6. `POST …/qc` —— VLM 衣着保真质检（同步），FAIL 给 `qc.suggest`；`POST …/retry` 按建议词自动重出（≤3 次）
  7. `POST …/video` —— 试穿图作首帧图生视频（5/10/15s 三档）
  8. `POST /v1/dressup/batch`（模特×服装×场景×姿势笛卡尔积，串行后台）、`POST /v1/dressup/export`（≤50 笔，可 zip）
- **同步/异步**：条目 CRUD 同步；生成/定妆照/批量异步（作业轮询同 `/v1/images/jobs` 语义）；qc 与属性分析同步（VLM）。
- **鉴权/隔离**：Bearer，owner 隔离（跨 owner 一律 404）——与 deer-flow 现有 per-user `h3t_` 令牌贯通机制（`MediaGatewayCredentialMiddleware`）天然兼容，**无需新鉴权工作**。
- **限流门禁**：VLM×渲染互斥（`resource_gate`），冲突 → 503 + `Retry-After`（客户端需按现有 `_parse_retry_after` 重试逻辑处理）。

### 2.2 真实网关验证记录（2026-11-02，全链路通过）

用 admin 令牌（研究用；生产走 per-user 令牌）在 `http://172.17.0.1:8000` 实测：

| 步骤 | 请求 | 结果 |
|---|---|---|
| 上传素材 | `POST /v1/media`（multipart，字段名 `file`，**单数**） | `med_89b0c0a5e07c`（人物全身图）、`med_60a8243a4a73`（上身泳装图） |
| 建模特 | `POST /v1/dressup/models` `{name, desc, source_media_id}` | 201 `dm_0e80c0`，`portrait_status=queued` 自动起定妆照作业 |
| 定妆照回填 | `GET /v1/dressup/models/dm_0e80c0` 轮询 | 约 50s → `done`，`portrait_url=/v1/images/jobs/img_261a57236778/file?...` |
| 建服装 | `POST /v1/dressup/garments` `{name, gtype:onmodel, media_id, keywords}` | 201 `dg_52b7f0` |
| 建试穿 | `POST /v1/dressup/tryons` `{model_id, garment_id}` | 201 `dt_c2ffb5`（同步台账） |
| 生成 | `POST …/generate` `{output:image, pose:front}` | 返回 `job_id=img_44fda8db9e84` + 组装好的中文提示词（含人物 desc、服装 keywords、影棚场景、姿势段） |
| 轮询 | `GET /v1/dressup/tryons/dt_c2ffb5` | 约 50s → `completed`，`outputs[0].url` 指向作业 file 端点；下载为 320x420 JPEG |
| 产物核验 | VLM 看图 | 试穿图正确呈现「深蓝+白拼色三角杯系带比基尼」，与服装 keywords 一致 |
| 质检 | `POST …/qc`（无 body，同步） | 返回 `{pass:false, answers:{颜色/款式/材质}, missing:[…], suggest:"建议补进提示词的属性：…"}` —— 质检链路可用（该图 pass=false 属合理判定：白底拼接细节与属性台账不完全一致） |

**结论**：契约与文档一致，全链路（素材→模特→服装→试穿→生成→质检）在真实网关可跑通，
端到端一次试穿生成约 100s（定妆照 50s + 试穿 50s）。无 deer-flow 侧阻塞项。

---

## 3. 对接方案：逐能力一节

> 统一原则（继承 design.md）：不动 deer-flow 领域状态机，全部以 **skill + 直调 curl 流程**
> 落地，复用 `media-gateway-api` skill 的凭证/轮询/错误处理章节；后端只在确有 provider
> 语义（可被 agent 工具直接调用）时才加代码。skill 内强制使用沙箱注入的
> `MEDIA_GATEWAY_TOKEN`（per-user），严禁 admin 令牌。

### 3.1 换装 skill：`dressup-tryon`（优先级 P0 · 工作量 M）

- **名称/触发词**：`dressup-tryon`；触发：换装 / 试衣 / 试穿 / 把这件衣服穿到模特身上 /
  dressup / try-on / 模特上身图。
- **流程**（skill 指导 agent 用 curl 直调，不需要后端新代码）：
  1. **输入收集**：人物图（至少 1 张，正/侧面最佳）+ 服装图（1–N 张）。已有素材库
     media_id 时直接用；否则 `POST /v1/media`（multipart 字段名 `file`）上传。
  2. **台账复用**：先 `GET /v1/dressup/models?owner=me`、`/garments?owner=me` 查重，
     已有条目直接引用，避免重复建档（定妆照一次 50s，复用是主要提速手段）。
  3. **建档**：模特 `{name, desc, source_media_id}`（desc 引导用户按「年龄·身材·脸型·发型」
     四件套给，缺省让 agent 从图生成）；服装优先 `/garments/from_media`（自动 VLM 属性分析，
     keywords 免手填）；场景可选 `/scenes/from_media`。
  4. **等待定妆照**：轮询 `GET /models/{id}` 至 `portrait_status=done`（约 1 分钟；
     失败不拦流程，仅 `portrait_url` 为空）。
  5. **建试穿 + 生成**：`POST /tryons` → `POST /tryons/{id}/generate`
     `{output:image, pose, aspect_ratio:"3:4"}` → 轮询 tryon 详情至 `completed`。
  6. **质检闭环**：`POST …/qc`；FAIL 且 `qc.suggest` 非空且 `retry_count<3` 时
     `POST …/retry` 自动重出（skill 写明上限 3 次，超限转人工/控制台）。
  7. **交付**：下载产物图给用户；批量/成套需求给 `media-console-link` 跳转。
- **扩展路径（可选，同 skill 后续增强）**：`output:video` / `POST …/video` 出走秀视频
  （walk/catwalk/turn，5/10/15s）；`POST /dressup/batch` 笛卡尔积批量（建议引导到控制台，
  批量任务时长不可控）。
- **复用关系**：凭证与轮询/错误信封完全复用 `media-gateway-api` skill §2/§3（直接在该
  skill 增补「换装端点速查」小节并新建 `dressup-tryon` skill 引用它）；控制台深度操作
  复用 `media-console-link`。
- **风险与注意**：① 503 VLM×渲染互斥需按 `Retry-After` 重试（skill 写明）；②
  `pose` 白名单外必须有 `pose_text`；③ 建档 body `extra=forbid`，多余字段 400；④
  一次生成 ≈1 分钟，skill 需管理用户预期（提示「生成中，约 1–2 分钟」）。
- **工作量**：M（skill 文档 + media-gateway-api 增补 + 单测 mock；后端零改动）。

### 3.2 组图生视频 tasks 直调（P2 · 不接，维持现状）

`/v1/tasks/groupvid|t2g` 是旧版直调入口，功能被 `POST /v1/media/{id}/generate` 编排路
（deer-flow `video_clips.py` 已用）覆盖。**建议不接**，仅在现有 `video-clips` skill 中
补一句说明避免误用。

### 3.3 分镜规划 plan 链（P2 · S）

`POST /v1/media/{id}/plan` + `/plan/estimate` + `/plan/board.md` + `/plan/submit`：
比 storyboard 更重的「规划→估价→确认→提交」链。deer-flow 的 `video-clips` 已走
storyboard + generate。建议：等用户出现「先估价/先看分镜板再生成」诉求时，在
`video-clips` skill 增补可选 plan 前置步骤。工作量 S，优先级 P2。

### 3.4 素材复用家族：replicate / clone / identity-frame / extract-frames（P1 · S）

- `POST /v1/media/{id}/replicate`（视频转可复刻素材）+ `GET /replicate`：从参考视频
  提取角色/场景资产，是「复刻同款视频」的入口，与 t2i-series/video-clips 互补。
- `POST …/identity-frame`：设定角色一致性锚定帧（提升组图人物一致性）。
- `POST …/extract-frames`、`/frame-scan`：视频抽帧/打分，供挑首帧。
- 建议：作为 `video-clips` / `t2i-series` skill 的进阶章节（不新建 skill），先接
  `identity-frame` + `extract-frames`（对成片质量有直接收益），replicate 待需求出现。
- 工作量 S；优先级 P1（次优先后）。

### 3.5 VLM 直调 `/v1/vlm/chat`（P3 · S，默认不做）

deer-flow 会话已有自有 VLM 通道；网关 VLM 只在「沙箱脚本想复用网关本地 qwen3vl、
不另起模型」时省资源。建议仅在 `media-gateway-api` skill 附录记一条端点，不建流程。
工作量 S；优先级 P3。

### 3.6 种子匹配 seedmatch（P3 · M，暂不接）

13 条端点 + 独立 seedmatch-serve，属研究型子系统（评分/预测/案例库），当前无用户
场景。建议维持 `media-console-link` 跳转；若后续要「参考图自动匹配种子并出图」，再
单独立项。工作量 M；优先级 P3。

### 3.7 规则库 rules（P3 · M，暂不接）

`/v1/rules/*`（参考片→分镜规则提取→按规则出 storyboard）与 drama-production 场景
相关但独立。建议先观察 drama-production skill 的实际使用反馈再决定是否并入。工作量
M；优先级 P3。

### 3.8 明确不接

`/v1/agent/*`（网关内嵌 Agent，与 deer-flow 定位冲突）、`/v1/model-services/*`、
`/v1/models*`、`/v1/queue|jobs|tags`（运维）、`/files/*`、`/summer/*`、`/drama/*` 页面
（控制台范畴，跳转已覆盖）。

---

## 4. 优先级与工作量汇总

| 优先级 | 项 | 形态 | 工作量 |
|---|---|---|---|
| **P0** | 换装 `dressup-tryon` skill | 新 skill + `media-gateway-api` 增补，后端零改动 | M |
| P1 | identity-frame / extract-frames | `video-clips`/`t2i-series` skill 进阶章节 | S |
| P2 | plan 分镜规划链 | `video-clips` 可选前置步骤 | S |
| P2 | tasks 直调旧入口 | 不接，仅文档标注 | — |
| P3 | VLM 直调 / seedmatch / rules | 附录记录或控制台跳转 | S–M |

**落地顺序建议**：按既有 rollout 纪律（mock 全绿再单独联调上线）——先写
`dressup-tryon` skill 与 mock 单测（`media_gateway/mock_server.py` 增加 dressup 路由），
全绿后用真实网关按 §2.2 流程做一次联调即上线；首个试点场景建议「电商模特上身图」
（单模特 × 单服装 × 3 姿势）。

## 5. 验证留档

- 验证日期：2026-11-02；产物样例 `/tmp/tryon_out.jpg`（320x420 JPEG，试穿图）。
- 涉及条目：`dm_0e80c0` / `dg_52b7f0` / `dt_c2ffb5` / 作业 `img_261a57236778`、
  `img_44fda8db9e84`（admin owner `u_c3d3a325`，可清理）。
- 注：deer-flow `backend/ref2..5.jpg` 为城市风光图，不适合换装样例；人物图取自网关仓库
  research 素材，服装图取自 `/tmp/tclothes_t05.0.jpg`。
