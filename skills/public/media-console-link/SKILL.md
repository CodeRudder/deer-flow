---
name: media-console-link
description: Use this skill when the user wants to open the media console (媒体控制台) — e.g. says 打开媒体控制台 / 去做换装 / 短剧制作 / 素材库管理 / 种子匹配 / VLM 打标, or asks for a link to the Media Gateway web UI. Also use it for 视频抽帧 / 帧提取 / extract frames — it returns a jump-link that opens the console's frame-extraction page directly on the right video. Does NOT extract frames itself.
---

# 媒体控制台跳转（media-console-link）

## 概述

当用户想进入媒体控制台（换装、短剧/视频编排、素材库、种子匹配、VLM 打标等网页功能）时，
调用后端 `POST /api/media-gateway/console-link` 获取跳转链接，并以 markdown 链接卡片的形式返回给用户。

**本 skill 不做任何生成工作**——生成类请求（生图/生视频）请使用对应的 `image-generation` /
`video-generation` skill；本 skill 只负责把用户安全地送到控制台页面。

## 如何获取链接

调用后端 API（使用当前会话的登录态，cookie 自动携带）：

```
POST /api/media-gateway/console-link
Content-Type: application/json

{}
```

成功响应：

```json
{ "url": "https://<媒体网关控制台地址>?ticket=<一次性票据>" }
```

要点：

- **身份永远来自当前登录用户**。API 不接受、也不要尝试传任何"指定用户/令牌"参数（无此参数）。
- 后端会用该用户在媒体网关的令牌换取一次性 ticket（约 60 秒有效、核销一次即失效），
  并拼成 `控制台地址?ticket=...`，用户点击后免登直达。
- 若后端返回 `400`，说明部署未配置 `MEDIA_GATEWAY_CONSOLE_URL`：告知用户"当前部署未配置媒体控制台地址，
  请联系管理员设置 `MEDIA_GATEWAY_CONSOLE_URL`"，**不要**自行拼 URL。
- 若后端返回 `404` 且提示"未置备"：说明该用户尚未开通媒体网关账号，引导用户先发起一次
  生图/生视频（会自动开通），之后重试本 skill。

## 降级行为

- 后端换 ticket 失败（网关抖动等）时会**自动降级**：仍返回 200，但 `url` 不带 `ticket` 参数。
  此时正常展示链接即可，并附一句："如跳转后要求登录，请在媒体控制台用你的账号直接登录。"
- ticket 已被使用或过期：控制台页面会自动回落到手动登录页，无需特殊处理。

## 返回格式（链接卡片）

拿到 `url` 后，用 markdown 输出一张简洁卡片，例如：

---

🎨 **媒体控制台已就绪**

[点击打开媒体控制台](<url>)

- 链接含一次性免登票据（约 60 秒内有效），请尽快打开
- 若票据过期会进入登录页，用你的账号登录即可
- 可在控制台进行：换装、短剧/视频编排、素材库管理、种子匹配、VLM 打标

---

## 使用示例

**例 1**
> 用户：打开媒体控制台
> 助手：（调用 `POST /api/media-gateway/console-link`）→ 输出上面的链接卡片。

**例 2**
> 用户：我想去做换装
> 助手：换装功能在媒体控制台完成。（调用 API 获取链接）→ 输出卡片，并补充一句"打开后进入『换装』页面即可上传人物图开始。"

**例 3（未配置）**
> 用户：帮我打开素材库
> 助手：（API 返回 400）→ "当前部署未配置媒体控制台地址，请联系管理员设置 `MEDIA_GATEWAY_CONSOLE_URL` 后再试。"

## 动作：视频抽帧引导（抽帧 / 帧提取 / extract frames）

**不做抽帧本身**——抽帧（扫描切分→选帧→存组图）在控制台的「视频抽帧」页完成，
本动作只负责生成直达链接并引导用户操作。

### 流程

1. **拿到控制台链接**：同上，调用 `POST /api/media-gateway/console-link` 拿 `url`。
2. **确定视频 media_id**：
   - 用户给了已入库视频的 media_id → 直接用。
   - 用户给的是**视频链接**（URL）→ 告知用户需先在控制台「链接抓取」页抓取入库拿到
     media_id，再回来要直达链接；或者直接给 `#vidframes` 基础页链接（见下方降级）。
   - 用户给的是**本地视频文件**→ 告知用户在控制台「素材 → 视频管理」上传入库拿到
     media_id； DeerFlow 侧没有视频上传 API，不要尝试替用户上传。
3. **拼直达 URL**：在 `url` 后面追加 hash：
   `<url>#vidframes?media=<media_id>`
   （例：`https://mgw.example/ui?ticket=abc#vidframes?media=med_v01`）。
   打开后控制台会落到「视频抽帧」页、**自动选中该视频并开始扫描**。
4. **输出链接卡片**（见下）。

### URL 能力说明

- `#vidframes` — 打开「视频抽帧」页（视频列表需手动选择）。
- `#vidframes?media=<id>` — 打开并自动选中该视频、自动开始扫描（列表里找不到该 id
  时静默回落到手动选择，不报错）。
- 参数在地址栏保留：刷新页面会重新定位到同一视频（不会重复自动扫描）。

### 返回格式（抽帧引导卡片）

🎬 **视频抽帧已就绪**

[点击打开视频抽帧页（已定位到你的视频）](<url#vidframes?media=...>)

- 打开后自动选中视频并开始扫描（长视频约 1-2 分钟），扫完点图勾选帧
- 选好后点「保存所选为组图」，组图可直接进组图生视频 / 换装素材

若视频尚未入库，先给 `#vidframes` 基础页链接，并在卡片里附一句：
"打开后在顶部下拉选择你的视频（或先在「链接抓取」/「视频管理」把视频入库）。"
