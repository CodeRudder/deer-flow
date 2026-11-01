# 验收标准（Acceptance Criteria）

> 配套 [design.md](design.md) / [workflow.md](workflow.md)。全部条目须在端到端环境实测通过。

## A0 连通性 / 配置

- [ ] 配齐 `MEDIA_GATEWAY_BASE_URL` / `ADMIN_TOKEN` / `CONSOLE_URL` 后，健康检查探针显示 MGW 可达（`GET /health`，`auth_mode=users`）；
- [ ] ADMIN_TOKEN 配置错误时，skill 返回明确"配置错误"提示，且**不回显令牌任何内容**；
- [ ] MGW 停机时，生图请求返回 retryable 错误，deer-flow 无崩溃、无悬挂请求（≤ 超时上限）。

## A1 账号打通（JIT 置备）

- [ ] deer-flow 新用户首次调用生图 → MGW 自动出现同名用户（role=user），映射表落库，`h3t_` 为密文；
- [ ] 第二次调用**不再**触发建号请求（审计日志/网关侧无重复 POST /v1/users）；
- [ ] 用户名含 MGW 非法字符/超 32 字符 → slug 化建号成功，映射可反查；
- [ ] 并发首调（同用户 2 并发）→ 仅建 1 个 MGW 用户、1 枚令牌（409 认领路径生效）；
- [ ] deer-flow admin 用户 → MGW 侧 role=admin；普通用户 → role=user；
- [ ] deer-flow 停用用户 → MGW 用户被停用：其存量会话与令牌立即失效（调任意业务端点 401）。

## A2 生图 / 生视频（API 通道）

- [ ] skill `media-generate` 文生图：提交 → 轮询 → 返回 deer-flow 代理产物 URL，前端 `<img>` 正常渲染（无 401）；
- [ ] 图生图（带参考图上传）全链路成功；
- [ ] 生视频：提交 → 作业创建 → 完成后产物经代理可播放；
- [ ] 重复提交带同一 `Idempotency-Key` → MGW 返回同一作业（无重复扣算力/作业）；
- [ ] skill `media-jobs`：按状态/时间过滤查询，字段与 MGW `/v1/jobs` 一致；
- [ ] **数据隔离**：用户 A 生成 N 个作业后，用户 B 经 `media-jobs` 查询**看不到** A 的任何作业（MGW 语义：404/空）；A 的产物代理 URL 被 B 的会话访问 → 403/404；
- [ ] A 的产物 URL 未登录访问 → 401；
- [ ] MGW 返回 429 → deer-flow 向前端透传 retryable + Retry-After，且无重试风暴（≤2 次）。

## A3 控制台跳转（免登）

- [ ] skill `media-console-link` 返回的 URL 打开后**免登**进入控制台，顶栏身份与 deer-flow 当前用户一致；
- [ ] ticket 只能用一次：同 URL 刷新第二次 → 回落手动登录页；
- [ ] ticket 超过 60s 未用 → 失效回落手动登录；
- [ ] URL 核销后地址栏不再含 ticket 参数；
- [ ] 未配置 CONSOLE_URL 时 skill 明确报配置缺失，不生成坏链接；
- [ ] 控制台内生成作业的 owner 为对应 MGW 用户（与 A1 置备一致）。

## A4 MGW 侧改动（ticket 端点）

- [ ] `POST /v1/auth/ticket`：有效 Bearer → 200 `{ticket, expires}`；无/坏令牌 → 401（统一错误信封）；
- [ ] 票据核销端点：正确 ticket → 发 `h3s_` 会话；二次使用/过期/伪造 → 拒绝；
- [ ] MGW 全量既有测试回归通过（ticket 端点未破坏既有鉴权路径）；
- [ ] 票据不出现在网关访问日志。

## A5 安全

- [ ] 前端任何响应（HTML/JSON/日志）中不出现 `h3t_`/`ADMIN_TOKEN` 明文；
- [ ] 映射表 DB 导出检查：令牌/密码列为密文；
- [ ] 代理路由不接受"指定任意用户令牌"的参数（永远取当前会话用户）。

## A6 回归

- [ ] deer-flow 既有 provider（openai_image 等）注册与调用回归通过；
- [ ] `make lint` / 后端单测全绿；新增模块单测覆盖：provisioner（含 409 认领、slug）、client 错误映射、代理路由鉴权。
