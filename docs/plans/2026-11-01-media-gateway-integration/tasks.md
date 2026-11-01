# 开发任务分解与端到端测试计划

> 配套 [design.md](design.md) / [workflow.md](workflow.md) / [acceptance.md](acceptance.md)。

## 阶段 M0：MGW 侧 ticket 端点（先行，向后兼容）

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| M0-1 | `POST /v1/auth/ticket` 签发端点（一次性、60s TTL、绑定用户；内存/短期存储） | `gateway/h3img/api.py` + 单测 | A4 |
| M0-2 | 票据核销端点 + `/ui` JS 增强（`?ticket=` → 换 `h3s_` → 清 URL） | 同上 | A4 |
| M0-3 | 日志脱敏确认（票据不入日志）；MGW 测试回归 | CI 绿 | A4 |

工期估计：1–2 人日。**部署后不影响任何既有调用方。**

## 阶段 M1：deer-flow 基础通道

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| M1-1 | `MediaGatewayClient`（Bearer/错误信封映射/Idempotency-Key/超时） | `media_gateway/client.py` + 单测 | A0、A2 错误映射 |
| M1-2 | `MediaGatewayProvisioner` + 映射表迁移（加密列） | `provisioner.py` + migration + 单测 | A1 |
| M1-3 | env 与配置加载、`.env.example` 文档 | config | A0 |
| M1-4 | 停用/角色变更联动钩子（接现有用户生命周期） | 钩子 + 单测 | A1 |

工期估计：3–4 人日。

## 阶段 M2：provider 接入 + 控制台跳转 skill

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| M2-1 | image provider `media_gateway` 注册：能力发现（映射为 `--describe-provider` 格式）/提交/轮询/产物代理 URL | registry + adapter + 单测 | A2 |
| M2-2 | video provider 同上 | 同上 | A2 |
| M2-3 | 产物代理路由 `/api/media-gateway/file/...`（当前用户令牌转发） | gateway 路由 + 单测 | A2/A5 |
| M2-4 | skill `media-console-link`（换装/素材库/种子匹配跳转） | skill + 说明 | A3 |
| M2-5 | 既有 skills 兼容性验证：`video-generation` / `image-generation` / `image-editing` 对 MGW 模型全流程（审批/轮询/任务查询/sidecar）不改一行跑通 | 集成测试 | A2 |
| M2-6 | skill `drama-production`（短剧编排：结构→托盘→cite→preview→生成→QC→分集；sidecar 状态） | skill + 单测 | A2b |
| M2-7 | skill `video-clips`（组图生视频编排：素材→plan/design→分镜板→提交→逐镜产物） | skill + 单测 | A2b |

**原则**：单镜生成不新增 skill（复用既有，见 design §4.1）；短剧与视频片段按
业务要求为独立编排型 skill（直调 MGW 多步 API，状态存 sidecar，不建 deer-flow 领域表）。

工期估计：7–9 人日（M2-6/7 编排 skill 较重）。

## 阶段 M3：端到端测试（E2E）

**环境**：1 台部署机同时跑 MGW（users 模式）与 deer-flow（docker dev 栈或本地 make dev）；
MGW 文档基线：`docs/integration/api/`。

| # | 用例 | 步骤概要 | 对应验收 |
|---|---|---|---|
| E2E-1 | 冷启动置备 | 新用户注册 → 首求生图 → 检查 MGW 用户/令牌/映射表 | A1 |
| E2E-2 | 生图全链路 | 既有 skill + MGW 模型：prompt→审批→提交→轮询→代理 URL 前端渲染；Idempotency 重放 | A2 |
| E2E-3 | 生视频全链路 | 既有 video-generation 工作流 + MGW 模型（含 sidecar、任务查询） | A2 |
| E2E-3b | 模型可选中立 | 同一 skill 分别用原生模型与 MGW 模型各跑一次，均成功；MGW 模型不支持的能力本地拒绝 | A2 |
| E2E-5b | 短剧全流程 | drama-production：小体量剧（1 集 2 镜）建结构→确认→生成→QC→分集；中断恢复（重开会话续跑） | A2b |
| E2E-5c | 视频片段全流程 | video-clips：素材→plan/design→分镜板→确认→提交→逐镜产物 | A2b |
| E2E-5d | 编排隔离 | 用户 A 建的剧/片段任务对用户 B 不可见（skill 与 API 双查） | A2b |
| E2E-4 | 多用户隔离 | 双用户各自生成；互查作业/互访产物 URL 均不可见 | A2 |
| E2E-5 | 控制台免登 | console-link 打开 → 免登 → 控制台内做一个换装 tryon → owner 正确 | A3 |
| E2E-6 | ticket 一次性/过期 | 刷新重放、等待过期 | A3/A4 |
| E2E-7 | 停用联动 | deer-flow 停用用户 → MGW 令牌/会话失效、控制台被踢出 | A1 |
| E2E-8 | 故障注入 | MGW 停机 / 429 / 令牌吊销三场景 | A0/A2 |
| E2E-9 | 安全巡检 | 抓包+日志+DB 导出，确认无明文令牌、代理不可越权 | A5 |
| E2E-10 | 回归 | 既有 provider/skills 回归 + 两仓 CI 全绿 | A6 |

**通过标准**：E2E-1~5d、10 全过；E2E-6~9 按表全过；所有验收条目勾选。
产出：E2E 报告（含 MGW 版本号、env 指纹、用例截图/日志摘录），落本目录 `e2e-report.md`。

## 里程碑与顺序

```
M0（MGW ticket）─┐
                 ├─► M3 E2E ─► 上线（MGW 先发，deer-flow 后发）
M1 ─► M2 ────────┘
```

- 总工期估计：8–11 人日（M0 可与 M1 并行）；
- 分支：deer-flow 用当前 `feat/gateway-media-api`；MGW 侧建议 `feat/auth-ticket`；
- 每阶段完成即在本文档勾选并附 PR 链接。
