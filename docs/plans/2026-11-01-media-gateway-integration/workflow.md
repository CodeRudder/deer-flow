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

## W2 生图 / 生视频（API 通道）

```
用户请求 ──► skill media-generate（参数：prompt / 参考图 / 时长等）
  │ 1. Provisioner.ensure_user()（幂等，通常直接命中）
  │ 2. provider adapter：MGW 能力发现 → 组装提交
  │ 3. POST /v1/... 生成接口，带：
  │      Authorization: Bearer <该用户 h3t_>
  │      Idempotency-Key: <uuid>（deer-flow 侧按"用户+请求语义"生成并短期缓存）
  │ 4. 返回作业 ID → skill 轮询 GET /v1/jobs?...（或用户追问时经 media-jobs 查询）
  │ 5. 完成后产物 URL 转 deer-flow 代理地址：
  │      /api/media-gateway/file/<path>（服务端带该用户令牌转发）
  ▼
前端 <img>/<video> 指向代理地址 → 正常渲染（无 401 问题）
```

错误映射：401→触发令牌重置流程（吊销旧+发新）；429/503→透传 retryable+Retry-After；
404→作业不存在或非本人（原样呈现，不区分）。

## W3 控制台跳转（深度功能：短剧/换装/组图等）

```
用户请求"做短剧/换装/组图" ──► skill media-console-link
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
