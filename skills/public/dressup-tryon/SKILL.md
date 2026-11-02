---
name: dressup-tryon
description: Use this skill when the user wants 换装 / 试衣 / 试穿 / 上身 / 模特上身图 / try-on / dressup — e.g. 把一件服装穿到人物/模特身上生成电商上身图、给服装出模特展示图、单模特×单服装×多姿势上身组图。经 Media Gateway（MGW）dressup API：素材上传 → 模特/服装台账复用（定妆照复用是主要提速点，重复模特不重建）→ 建试穿 → 异步生成轮询 → VLM 衣着保真质检闭环（FAIL 自动 retry ≤3 次）→ 产物落盘交付。需要任何图片 API Key 吗？不需要——走平台注入的 per-user MGW 凭证。
---

# 换装试穿 / 模特上身图（dressup-tryon）

## 执行方法（必读）

**一次试穿只跑一条命令**（前台同步执行，脚本内部完成上传/台账复用/建档/生成/轮询/QC 闭环/下载）：

```bash
python /mnt/skills/public/dressup-tryon/scripts/tryon.py \
  --person /mnt/user-data/uploads/person.jpg \
  --garment /mnt/user-data/uploads/garment.jpg \
  --poses front side back \
  --out-dir /mnt/user-data/outputs/dressup/<slug>
```

- 输入图支持会话内产物/上传路径（`/mnt/user-data/…`）；也接受已入库的
  media_id：`--person-media-id med_…` / `--garment-media-id med_…` 代替文件路径
  （**先查台账再上传**——重复模特不重建，定妆照一次约 50s，复用是主要提速手段）。
- 凭证由平台注入沙箱 env（`MEDIA_GATEWAY_BASE_URL` / `MEDIA_GATEWAY_TOKEN`），
  脚本自动使用当前用户凭证——**禁止**自行置备账号或读取/复用其他用户的令牌，
  **严禁** admin 令牌。`MEDIA_GATEWAY_BASE_URL` 支持逗号分隔多候选地址（如
  `http://172.17.0.1:8000,http://192.168.1.10:8000`），自动选用第一个可用者；
  具体地址以 env 注入为准，不要手写 IP:端口直连。
- 常用参数：`--poses`（预设 `front/side/back/stool/walking/bag`，缺省
  `front side back` = 单模特×单服装×3 姿势电商上身图）、`--pose-text`（白名单外
  的自定义姿势描述，可与 `--poses` 混用）、`--model-name/--model-desc`
  （desc 建议按「年龄·身材·脸型·发型」四件套给，缺省从图自动生成）、
  `--garment-name/--garment-keywords`（keywords 缺省走网关自动 VLM 属性分析）、
  `--max-retries`（QC 失败自动重出上限，缺省 3）、`--timeout`（每次生成轮询
  总超时，缺省 300s；实测一次生成约 1 分钟）、`--out-dir`。
- 产物写入 out-dir：`01-front.png` / `02-side.png`…（按姿势命名）；每步进度打
  到 stderr，结束时 stdout 打印 JSON 摘要（`images[]`：pose / tryon_id /
  qc / retries / file / url）。

**严禁以下做法**：

- ❌ 在沙箱里手写驱动脚本/curl 轮询——生成与定妆照都是异步作业（提交 → 轮询
  → 终态取产物），脚本已在前台封装全部轮询与 QC 闭环，等命令跑完即可；
- ❌ `nohup`、`&`、tmux 等任何后台化；
- ❌ 自行置备 MGW 账号/申请令牌，或使用 `MEDIA_GATEWAY_ADMIN_TOKEN`。

**交付硬性要求**：生成成功后**必须**调用 `present_files` 交付 out-dir 中的**全部**
上身图——未交付即视为任务未完成，不要反问用户。QC 结论（pass / missing /
suggest）在摘要 JSON 里，逐张转述给用户；`pass=false`（重试 3 次仍 FAIL）的
图照常交付并明确说明"衣着细节与属性台账有出入，建议人工目检或到控制台重出"。

## 流程（脚本内已编排；这里说明每步发生了什么）

1. **素材上传**：人物图（至少 1 张，正/侧面全身最佳）+ 服装图（1 张，上身照
   最佳）经 `POST /v1/media`（multipart，字段名 `file` **单数**）入库。
2. **台账复用**：`GET /v1/dressup/models`、`GET /v1/dressup/garments` 查重——
   同 `source_media_id` / 同 `media_id` 的条目直接引用，**不重复建档**
   （定妆照 50s + 生成 1 分钟，复用省一半时间）。
3. **建档**：模特 `POST /v1/dressup/models`（201 即回，`portrait_status=queued`
   自动起定妆照作业；脚本轮询至 `done`，失败不拦流程）；服装优先走
   `POST /v1/dressup/garments/from_media`（自动 VLM 属性分析，keywords 免手填）。
4. **建试穿 + 生成**：`POST /v1/dressup/tryons`（同步台账）→
   `POST …/generate`（异步作业，`output:image`，pose 预设或 `pose_text`）→
   轮询 tryon 详情至 `completed`，产物 URL 指向 `/v1/images/jobs/{id}/file`。
5. **VLM 质检闭环**：`POST …/qc`（同步）——`pass=true` 输出交付；
   `pass=false` 且有 `suggest` → 自动 `POST …/retry` 按建议词重出，**上限 3 次**；
   `missing`/`suggest` 附在结果里转述。超限仍 FAIL → 转人工说明（或引导控制台）。
6. **产物落盘**：逐张下载到 out-dir，调用 `present_files` 交付。

给用户的预期管理：首次（含定妆照）约 2 分钟，复用模特后每姿势约 1 分钟；
生成中不要催促或中断命令。

## 边界与引导

- **批量（模特×服装×场景×姿势笛卡尔积）**：本 skill 只做单模特×单服装×N 姿势；
  成套批量任务时长不可控，引导用户到媒体控制台（用 `media-console-link` skill
  取免登链接）。
- **试穿视频**（`output:video` / walk-catwalk-turn 走秀）：脚本暂不封装；有需求时
  引导控制台，或在用户明确要求后按 media-gateway-api skill 的换装端点速查直调。
- 素材库管理（改名/删除/补属性）、定妆照重出：控制台操作，`media-console-link`。

## 错误处理

| 现象 | 处理 |
|---|---|
| `503` + `Retry-After` | VLM×渲染互斥（资源门禁）；按头等待后重试，脚本已自动处理 |
| `429 queue_full` / 限流 | 遵守 `Retry-After`，稍后重试，不做重试风暴 |
| `400 invalid_request` | 常见：pose 白名单外没给 `pose_text`、素材未上传、建档 body 多余字段（网关 `extra=forbid`）；改参数重跑 |
| `404` | 条目不存在或越权（owner 隔离一律 404）；确认 media_id 属于当前用户 |
| 定妆照 `failed` | 不拦流程：`portrait_url` 为空时生成质量可能下降，提示用户可到控制台重出定妆照 |
| QC 重试 3 次仍 FAIL | 照常交付并附 `suggest` 说明，引导人工目检/控制台 |
