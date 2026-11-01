# 工作流程（Workflow）

> 配套 [design.md](design.md)。描述各场景的端到端流程与时序。

## W1 首次使用：JIT 置备

```
用户（deer-flow 已登录）
 │ 1. 对话中请求"生成一张图/一段视频"或"打开媒体控制台"
 ▼
deer-flow 后端
 │ 2. 查 media_gateway_users 映射表
 │    ├─ 命中 → 取存量 h3t_（解密）→ 跳到 W2/W3
 │    └─ 未命中 ↓
 │ 3. Provisioner（带 MEDIA_GATEWAY_ADMIN_TOKEN）：
 │    a. POST /v1/users      建号（随机密码；用户名 slug 规则见 design §5.1）
 │    b. POST /v1/users/{uid}/tokens  发 h3t_（label="deerflow"）
 │    c. 令牌/密码加密写映射表
 │ 4. 审计日志（不含明文）
 ▼
后续请求使用该用户 h3t_
```

失败分支：
- `POST /v1/users` 409（用户名已存在）→ 按 username 查 MGW 认领，仅补发令牌；
- MGW 不可达 → 返回 retryable 错误，不写半截映射（建号成功但发令牌失败时重试只补令牌）。

## W2 生图 / 生视频（API 通道，复用既有 skills/工作流）

```
用户请求（可指定模型，如"用媒体网关的 XX 模型生成…"）
  │ 1. 既有 skill（video-generation / image-generation / image-editing）
  │    按现规则解析 provider（--model > env 默认 > provider 缺省）
  │    → 路由到 media_gateway provider
  │ 2. skill 调 adapter 的能力发现（映射 MGW 能力 API → --describe-provider 格式）
  │    → 拟稿/审批（既有流程不变）
  │ 3. Provisioner.ensure_user()（幂等）→ 取该用户 h3t_
  │ 4. adapter 提交 MGW，带：
  │      Authorization: Bearer <该用户 h3t_>
  │      Idempotency-Key: <uuid>（deer-flow 侧按"用户+请求语义"生成并短期缓存）
  │ 5. 既有 skill 的 async create → poll → download 流程照常轮询 MGW
  │ 6. 完成后产物 URL 转 deer-flow 代理地址：
  │      /api/media-gateway/file/<path>（服务端带该用户令牌转发）
  ▼
前端 <img>/<video> 指向代理地址 → 正常渲染（无 401 问题）
```

要点：**不新增生成类 skill**——既有工作流（审批、轮询、任务查询、sidecar）
对 MGW 模型全部生效；用户在 deer-flow 原有模型里照常选用 MGW 模型或原生模型。

错误映射：401→触发令牌重置流程（吊销旧+发新）；429/503→透传 retryable+Retry-After；
404→作业不存在或非本人（原样呈现，不区分）。

## W3 控制台跳转（换装/素材库/种子匹配等）

```
用户请求"换装/素材库管理" ──► skill media-console-link
  │ 1. Provisioner.ensure_user()
  │ 2. 后端：POST /v1/auth/ticket（Bearer 该用户 h3t_）→ 一次性 ticket（60s）
  │ 3. 返回链接卡片：[打开 Media Gateway 控制台]({CONSOLE_URL}?ticket=...)
  ▼
浏览器新标签打开
  │ 4. /ui JS 检测 ?ticket= → 核销换 h3s_ 会话 → replaceState 清参数
  │ 5. 用户在控制台操作短剧/换装/组图/素材库（MGW 全功能）
  ▼
会话 7 天有效（h3s_），期间再次跳转可直接命中存量会话
```

降级：ticket 过期/已用 → 控制台显示手动登录（用户名/密码来自置备时的随机密码，
管理员可从 deer-flow 用户管理页"重置 MGW 密码"→ 调 admin API 重置后告知用户）。

## W3a 短剧制作（编排型 skill `drama-production`）

```
用户："制作短剧《XX》第一集"
  │ 1. Provisioner.ensure_user() → h3t_
  │ 2. 建结构：POST 剧 → 章 → 情节 → 片段/镜头（MGW drama API，分段展示给用户确认）
  │ 3. 资产托盘：登记角色/场景/道具 → MGW 自动编号与重映射
  │ 4. cite 引用整理/翻译 → prompt_preview 校验（零 GPU，直接返回校验结果）
  │ 5. 用户确认 → 逐镜提交生成（每笔带 Idempotency-Key，串行/受控并发）
  │ 6. 轮询各镜作业 → 完成 → QC；不通过：≤2 次自动重试，仍失败回交用户决策
  │ 7. 全镜通过 → MGW 自动分集 → 产物 URL 转 deer-flow 代理地址
  │ 8. 过程状态（结构/分镜/QC 记录）写任务工作目录 sidecar 文件
  ▼
交付：分集产物 + sidecar 文件；MGW 侧为数据真源（可续作下一集）
```

## W3b 视频片段（编排型 skill `video-clips`，组图生视频）

```
用户："用这些参考图生成分镜视频"
  │ 1. ensure_user() → h3t_；素材经 MGW 素材库（上传/抓取入库，或引用已有）
  │ 2. plan/design：设计方案（MGW plan/design API）→ 展示用户确认
  │ 3. 分镜板：逐镜设计图（可 t2g 文生分镜）→ 用户确认
  │ 4. 提交生成 → 轮询 → 逐镜产物（代理 URL）→ QC
  ▼
交付：逐镜产物 + sidecar；未覆盖的管理操作（克隆/批量整理）引导控制台跳转
```

## W4 生命周期联动

| 事件（deer-flow） | 动作（→ MGW） |
|---|---|
| 用户停用/删除 | `PATCH /v1/users/{uid}` 停用 → 其会话/令牌即时失效 |
| 用户角色变更 | `PATCH /v1/users/{uid}` 同步 role（单向） |
| 令牌 401 | 吊销旧令牌（按映射表存 sha8）→ 重发一枚 → 更新映射表 |
| deer-flow 侧卸载媒体集成 | 可选：批量吊销 label="deerflow" 令牌（不删 MGW 用户与作业） |

## W5 部署 / 配置流程

1. MGW：`users` 模式（users.json 存在）、生成一枚 admin `h3t_`（`POST /v1/users/{uid}/tokens`）；
2. deer-flow：配置 §3 env（BASE_URL / ADMIN_TOKEN / CONSOLE_URL），迁移新增映射表；
3. 冒烟：`W0`（见 acceptance.md A0）连通性检查；
4. 发布顺序：**MGW ticket 端点 + /ui JS 先上（向后兼容），deer-flow 后上**。
