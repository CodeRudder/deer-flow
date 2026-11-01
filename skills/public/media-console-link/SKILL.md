---
name: media-console-link
description: Use this skill when the user wants to open the media console (媒体控制台) — e.g. says 打开媒体控制台 / 去做换装 / 短剧制作 / 素材库管理 / 种子匹配 / VLM 打标, or asks for a link to the Media Gateway web UI. Returns a jump-link card pointing at the user's own media console session.
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
