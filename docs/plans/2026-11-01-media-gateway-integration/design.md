# 媒体网关（Media Gateway）集成设计方案

> 日期：2026-11-01 · 状态：设计定稿
> 原则：**不深度改造 deer-flow**，以 skill + provider 为主；深度功能（短剧/换装/组图等）
> 通过引用网关控制台 URL 跳转完成。网关侧仅一处小改动（一次性登录票据）。

## 1. 背景与目标

- 媒体网关（下称 **MGW**，仓库 `minimax-h3-gateway`，FastAPI 实现 `gateway/h3img/`）提供：
  生图/生视频、素材库、组图生视频、短剧、换装、种子匹配、VLM 等 131+ 路由，
  `Bearer` 鉴权、`users` 模式多用户、作业按 owner 隔离。
  对接文档：MGW 仓库 `docs/integration/api/`（images / media / groupvid / drama /
  dressup / jobs / seedmatch / auth / ops 分册）。
- deer-flow 已有 image/video generation provider registry（
  `backend/packages/harness/deerflow/models/{image_generation,video_generation}/registry.py`，
  现有 `openai_image` 等 provider）与 skills 体系。

**目标**：
1. deer-flow 用户可在对话/工作流中使用 MGW 的生图/生视频能力；
2. deer-flow 登录身份贯通到 MGW（API + 控制台两条通道），多用户数据隔离由 MGW 保证；
3. 短剧/换装/组图等深度功能以控制台链接方式提供给用户，不在 deer-flow 重建领域模型。

**非目标**：
- 不在 deer-flow 实现 MGW 领域状态机（drama/dressup/groupvid）；
- 不 iframe / 不打包 / 不反代 MGW 前端代码；
- 不修改 MGW 鉴权体系（仅新增一次性票据端点）。

## 2. 总体架构

```
deer-flow 用户（better-auth 会话）
   │
   ├─ 生图/生视频请求 ──► MediaGatewayProvisioner（JIT 建号/发令牌）
   │        │                 │ 首次：POST /v1/users + POST /v1/users/{uid}/tokens
   │        ▼                 ▼ 映射表 media_gateway_users（令牌加密落库）
   │   provider adapter（media_gateway）──Bearer <该用户 h3t_>──► MGW /v1/*
   │                                                            │ owner 归属该用户
   ├─ skill: media-generate / media-jobs（走同一 provider 通道）
   │
   └─ skill: media-console-link ──► 后端用该用户 h3t_ 换一次性 ticket
            │                      返回 {CONSOLE_URL}?ticket=…
            ▼
      浏览器新标签打开 MGW /ui?ticket=… ──► 换 h3s_ 会话，免登进入控制台
                                           （短剧/换装/组图等深度功能在此操作）
```

## 3. 命名与配置

网关通用名称：**Media Gateway（MGW）**（不再使用 "h3" 作为对外名称）。

deer-flow 侧新增 env（`.env.example` 同步）：

```env
# ── Media Gateway ─────────────────────────────────────────────
# API 基址（后端程序调用，拼 /v1/...）
MEDIA_GATEWAY_BASE_URL=http://<mgw-host>:8000
# 置备专用 admin 长期令牌（仅用于建号/发令牌，不用于日常生成）
MEDIA_GATEWAY_ADMIN_TOKEN=
# 控制台页面（仅用于生成给用户点击的跳转链接）
MEDIA_GATEWAY_CONSOLE_URL=http://<mgw-host>:8000/ui
# 可选：默认模型
# MEDIA_GATEWAY_IMAGE_MODEL=
# MEDIA_GATEWAY_VIDEO_MODEL=
```

## 4. 模块设计（deer-flow 侧，全部为增量）

| 模块 | 位置（建议） | 职责 |
|---|---|---|
| `MediaGatewayClient` | `backend/packages/harness/deerflow/media_gateway/client.py` | HTTP 封装：Bearer 注入、错误信封映射（401/404/409/429/502/503 + `Retry-After`）、`Idempotency-Key` 生成 |
| `MediaGatewayProvisioner` | 同目录 `provisioner.py` | JIT 建号/发令牌/停用联动（见 §5） |
| 映射表 | gateway 侧 DB 迁移：`media_gateway_users(deerflow_user_id PK, mgw_user_id, mgw_username, mgw_token_enc, mgw_password_enc, created_at, updated_at)` | 用户↔网关账号映射；令牌 AES 加密落库 |
| image provider | 在 `image_generation/registry.py` 注册 `media_gateway` provider | 能力发现/提交/轮询/产物 URL 转换（产物经 deer-flow 服务端代理转发，带用户令牌） |
| video provider | 在 `video_generation/registry.py` 注册 `media_gateway` provider | 同上；对接 MGW 视频（pay-as-you-go 作业模型） |
| skill `media-generate` | skills 目录 | 文生图/图生图/生视频；agent 编排入口 |
| skill `media-jobs` | skills 目录 | 查询作业状态/历史（映射 MGW `/v1/jobs` 查询参数） |
| skill `media-console-link` | skills 目录 | 返回控制台跳转卡片（含一次性 ticket URL） |
| 产物代理路由 | gateway 层 `/api/media-gateway/file/...` | 服务端带用户令牌转发 MGW 媒体端点，解决 `<img src>` 无 Authorization 头 401 |

## 5. 账号打通与身份传递

### 5.1 JIT 置备（自动创建网关用户）

触发：deer-flow 用户首次触达媒体功能（调 provider / 申请控制台链接）。

```
查 media_gateway_users → 命中：直接用存量 h3t_ 令牌
无记录：
  1) POST /v1/users  {username: <deerflow用户名>, role: <映射>, password: <随机32字节>}
     · 用户名规则：直接用 deer-flow 用户名；MGW 约束（≤32 字符、无空白/`/`/`:`）
       不满足时 slug 化（并保证映射表唯一）
     · 409（已存在）→ 按 username 反查认领，不重复建号
  2) POST /v1/users/{uid}/tokens {label: "deerflow"} → h3t_（明文仅此一次）
  3) 令牌与随机密码 AES 加密落库（密码仅作控制台手动登录兜底）
```

角色映射：deer-flow admin → MGW `admin`；普通用户 → MGW `user`。单向同步。

### 5.2 API 通道身份传递

provider adapter / skills 调 MGW 一律带**当前登录用户**的 `h3t_` 令牌
（服务端解密取用，绝不向浏览器泄露）。MGW 解析 owner → 作业/素材归属该用户；
`user` 角色访问他人资源统一 404，隔离由 MGW 现成机制保证。

**禁止**使用服务账号/Aadmin 令牌做日常生成请求（会破坏 owner 隔离）。

### 5.3 控制台通道身份传递（MGW 侧唯一改动）

MGW 新增一次性票据端点（设计）：

- `POST /v1/auth/ticket`，权限：auth（任意有效用户令牌）
- 行为：签发一次性票据（随机 32 字节，服务端短期内存或落库；TTL 60s，单次使用，绑定签发用户）
- 响应：`{"ticket": "<随机串>", "expires": <unix 秒>}`
- 控制台 `/ui` JS 增强：检测 `?ticket=` → `POST /v1/auth/login-ticket`（或同端点核销）
  换取 `h3s_` 会话 → `history.replaceState` 清除 URL 参数
- 安全：仅 HTTPS、一次性、短 TTL、绑定用户；票据不落日志

用户停用联动：deer-flow 停用/删除用户 → `PATCH /v1/users/{uid}` 停用 MGW 用户
→ 其全部会话与令牌立即失效。

## 6. 能力对接范围

| MGW 能力 | deer-flow 承载方式 |
|---|---|
| 生图 / 生视频 | provider `media_gateway` + skill `media-generate`（API 直连） |
| 作业查询 / 历史 | skill `media-jobs`（API 直连） |
| 媒体产物读取 | deer-flow 代理路由（带用户令牌转发） |
| 素材库 / 组图生视频 / 短剧 / 换装 / 种子匹配 / VLM | 控制台链接跳转（skill `media-console-link`），不在 deer-flow 实现 |

## 7. 失败与降级

| 场景 | 行为 |
|---|---|
| MGW 不可达（503/连接失败） | provider 返回可重试错误；skill 提示稍后再试，附 `Retry-After` |
| 置备时 admin 令牌无效 | skill 返回明确配置错误提示（不含令牌内容） |
| 令牌失效（401） | 尝试重新置备一枚（旧令牌先吊销）；仍失败则报错 |
| 控制台 ticket 过期/已用 | 控制台回落到手动登录页 |
| 429 | 透传限流语义（retryable + Retry-After），不本地重试风暴 |

## 8. 安全要点

- `MEDIA_GATEWAY_ADMIN_TOKEN` 与用户 `h3t_` 只存服务端（env / DB 加密列），不出后端；
- 映射表加密列的密钥走现有 secret 管理方式；
- ticket 一次性 + 60s TTL；控制台核销后立即从 URL 清除；
- 审计：置备/停用联动/票据签发均写 deer-flow 日志（不含令牌明文）。
