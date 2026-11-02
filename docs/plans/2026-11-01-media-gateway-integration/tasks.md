# 开发任务分解与端到端测试计划

> 配套 [design.md](design.md) / [workflow.md](workflow.md) / [acceptance.md](acceptance.md)。

## 阶段 M0：MGW 侧对接必需改造【现行，立即开发】

> 2026-11-01 决策：**对接必需的网关改造现在做、不做过渡版本**（ticket 免登是
> "登录身份传递到控制台"的必需机制，属对接范围）；**高级扩展功能后置**（如
> 种子择优增强等）。网关改动在独立分支 `feat/auth-ticket` 单点提交，避免与
> 网关侧并行开发冲突。M1/M2 其余所需能力（admin 建号、per-user 令牌、owner
> 隔离、生成/轮询/产物、Idempotency-Key、`/ui`）网关现有 API 已具备，零改动。

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| M0-1 | `POST /v1/auth/ticket` 签发端点（一次性、60s TTL、绑定用户；内存短期存储） | `gateway/h3img/api.py` + 单测 | A4 |
| M0-2 | 票据核销端点 + `/ui` JS 增强（`?ticket=` → 换 `h3s_` → 清 URL；失败回落登录页） | 同上 | A4 |
| M0-3 | 日志脱敏确认（票据不入日志）；MGW 全量测试回归 | CI 绿 | A4 |

工期估计：1–2 人日。**向后兼容，不影响既有调用方。**

高级扩展（后置，不占当前工期）：种子择优增强、控制台内嵌 deer-flow 会话、其它非对接必需项。
对接中如发现新缺口：先记录到"缺口清单"→ 判定是否对接必需 → 必需则单点立项即时开发。
缺口清单：（暂无）


## 阶段 M1：deer-flow 基础通道

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| M1-1 | `MediaGatewayClient`（Bearer/错误信封映射/Idempotency-Key/超时） | `media_gateway/client.py` + 单测 | A0、A2 错误映射 |
| M1-2 | `MediaGatewayProvisioner` + 映射表迁移（加密列） | `provisioner.py` + migration + 单测 | A1 |
| M1-3 | env 与配置加载、`.env.example` 文档 | config | A0 |
| M1-4 | 停用/角色变更联动钩子（接现有用户生命周期） | 钩子 + 单测 | A1 |

工期估计：3–4 人日。

## 阶段 M2：provider 接入 + 控制台跳转 skill

> 2026-11-01 调整：任务按**从简单到复杂**重排为 5 个批次，分批交付——
> 批次 1 完成即可打通端到端基础链路（跑 E2E-mock 基础项），后续批次逐层加码。
> 任务 ID 与产出/验收列保持不变。

### 批次1（最简，打通端到端）

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| 批次1 / M2-1 | image provider `media_gateway` 注册：能力发现（映射为 `--describe-provider` 格式）/提交/轮询/产物代理 URL | registry + adapter + 单测 | A2 |
| 批次1 / M2-2 | video provider 同上 | 同上 | A2 |
| 批次1 / M2-3 | 产物代理路由 `/api/media-gateway/file/...`（当前用户令牌转发） | gateway 路由 + 单测 | A2/A5 |
| 批次1 / M2-4 | skill `media-console-link`（换装/素材库/种子匹配跳转） | skill + 说明 | A3 |
| 批次1 / M2-5 | 既有 skills 兼容性验证：`video-generation` / `image-generation` / `image-editing` 对 MGW 模型全流程（审批/轮询/任务查询/sidecar）不改一行跑通 | 集成测试 | A2 |
| 批次1 / （配套） | mock gateway 测试夹具（按 mocks/E2E-mock 约定，覆盖 auth/users/生成/轮询/产物契约） | pytest 夹具 + httpx MockTransport | E2E-mock 基础项 |

→ **批次 1 完成 = 可跑 E2E-mock 基础链路**（E2E-1/2/4/8/9/10 基础项，见 M3 标注）。

### 批次2

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| 批次2 / M2-6 | skill `drama-production`（创作智能体：LLM 剧本编写→MGW 落库→五要素分镜设计→t2g 分镜图→设计层校验→生成→QC 迭代闭环→分集；checkpoint/auto 双模式） | skill + prompts + 单测 | A2b |
| 批次2 / M2-8 | 迭代闭环通用件：QC 归因分类器（prompt/引用/种子）、修正策略、版本追加与回滚（全走 MGW 记录） | 模块 + 单测 | A2b |

### 批次3

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| 批次3 / M2-7 | skill `video-clips`（同上，组图生视频路径：素材→plan/design→五要素分镜→分镜板→QC 闭环→逐镜产物） | skill + prompts + 单测 | A2b |

### 批次4

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| 批次4 / M2-9 | skill `t2i-series`（文生组图：LLM 组图设计→MGW 组图生成→QC 闭环） | skill + 单测 | A2c |
| 批次4 / M2-10 | skill `image-text-post`（图文帖子：组图+文案+版式组装+VLM 抽检） | skill + 单测 | A2c |
| 批次4 / M2-12 | skill `dressup-tryon`（换装试穿 P0 试点：素材上传→台账复用（定妆照不重建）→建试穿→异步生成轮询→VLM 质检闭环（FAIL 自动 retry ≤3，网关超限 409 转人工）→产物落盘；单模特×单服装×3 姿势缺省） | skill + scripts/tryon.py + 编排层 `media_gateway/dressup.py` + mock dressup 路由 + 单测 15 例 | A2c（✅ 2026-11-02：mock 全绿 + 真实网关冒烟全链路通过，见 dressup-and-remaining-apis.md §5） |

### 批次5（最复杂）

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| 批次5 / M2-11 | skill `content-series`（顶层内容矩阵：输入适配→规划→路由复用各 skill→完成度追踪→续作；风格指南/视觉资产落 MGW） | skill + prompts + 单测 | A2c |

**原则**：单镜生成不新增 skill（复用既有，见 design §4.1）；短剧与视频片段为
**创作智能体型** skill——LLM 驱动"编写→精细设计→检验→修正"闭环，
数据全部经 MGW API 落库（MGW 为真源），deer-flow 仅存断点游标，不建领域表。

工期估计：14–18 人日（M2-6~11 创作智能体与内容矩阵较重），按批次分批交付；
批次 1 约 4–5 人日即可端到端跑通 E2E-mock。

## 阶段 M3：端到端测试（E2E）

**环境分两档（2026-11-01 拍板：开发测试用 mock，联调用真实网关）**：
- **E2E-mock**（日常可跑，CI 友好）：pytest 夹具/httpx MockTransport 级 mock gateway，
  覆盖 auth/users/生成提交/轮询/产物 URL 契约（错误信封、Idempotency、409/429/503 语义
  按 MGW `docs/integration/api/` 建模）；deer-flow 走 docker dev 栈或本地 make dev。
- **E2E-real**（联调验收门）：1 台部署机同时跑真实 MGW（users 模式 + 真实生成引擎）
  与 deer-flow；只在 M2 开发完毕后执行。

| # | 用例 | 步骤概要 | 对应验收 |
|---|---|---|---|
| E2E-1 | 冷启动置备 | 新用户注册 → 首求生图 → 检查 MGW 用户/令牌/映射表 | A1 |
| E2E-2 | 生图全链路 | 既有 skill + MGW 模型：prompt→审批→提交→轮询→代理 URL 前端渲染；Idempotency 重放 | A2 |
| E2E-3 | 生视频全链路 | 既有 video-generation 工作流 + MGW 模型（含 sidecar、任务查询） | A2 |
| E2E-3b | 模型可选中立 | 同一 skill 分别用原生模型与 MGW 模型各跑一次，均成功；MGW 模型不支持的能力本地拒绝 | A2 |
| E2E-5b | 短剧全流程（**随批次2解锁**） | drama-production auto 模式：从一段创意自动完成 剧本→五要素分镜→t2g 目检→生成→QC 迭代闭环→分集；含至少一次"QC 失败→自动修正→通过"；中断恢复 | A2b |
| E2E-5c | 视频片段全流程（**随批次3解锁**） | video-clips：素材→LLM 五要素分镜设计→分镜板→QC 闭环→逐镜产物 | A2b |
| E2E-5e | 内容矩阵（**随批次5解锁**） | content-series：一个想法 → ≥3 选题×≥2 格式全自动生产（含 1 次 QC 修正）；系列一致性目检 | A2c |
| E2E-5d | 编排隔离（**随批次2+解锁**） | 用户 A 建的剧/片段任务对用户 B 不可见（skill 与 API 双查） | A2b |
| E2E-4 | 多用户隔离 | 双用户各自生成；互查作业/互访产物 URL 均不可见 | A2 |
| E2E-5 | 控制台免登 | console-link 打开 → 免登 → 控制台内做一个换装 tryon → owner 正确 | A3 |
| E2E-6 | ticket 一次性/过期 | 刷新重放、等待过期 | A3/A4 |
| E2E-7 | 停用联动 | deer-flow 停用用户 → MGW 令牌/会话失效、控制台被踢出 | A1 |
| E2E-8 | 故障注入 | MGW 停机 / 429 / 令牌吊销三场景 | A0/A2 |
| E2E-9 | 安全巡检 | 抓包+日志+DB 导出，确认无明文令牌、代理不可越权 | A5 |
| E2E-10 | 回归 | 既有 provider/skills 回归 + 两仓 CI 全绿 | A6 |

**上线节奏（2026-11-01 拍板）**：每个功能开发完成 + E2E-mock 全绿后，立即做该功能的
**单独真实网关对接验证**（真实 MGW http://127.0.0.1:8000 + 真实引擎），通过即上线该功能，
不等全部完成。顺序：生图/生视频 provider → console-link → drama-production → video-clips
→ t2i-series/image-text-post → content-series。

**通过标准**：E2E-1~5d、10 全过；E2E-6~9 按表全过；所有验收条目勾选。
产出：E2E 报告（含 MGW 版本号、env 指纹、用例截图/日志摘录），落本目录 `e2e-report.md`。

**与 M2 批次的对应（分批交付节奏）**：
- **批次 1 完成即可跑**：E2E-1/2/4/8/9/10（冷启动置备、生图全链路、多用户隔离、故障注入、安全巡检、回归）——基础链路 + mock 夹具就绪；
- **随批次解锁**：E2E-5b（批次2，drama-production）、E2E-5d（批次2+）、E2E-5c（批次3，video-clips）、E2E-5e（批次5，content-series）；
- 其余（E2E-3/3b/5/6/7）属基础链路范畴，批次 1（配合 M0/M1）即可覆盖。

## 里程碑与顺序

```
M0（MGW ticket，独立分支）─┐
                           ├─► M3 E2E ─► 上线（MGW 先发，deer-flow 后发）
M1 ─► M2 ──────────────────┘
      └─ 分批：批次1（provider注册+代理+console-link+mock夹具，端到端跑通）
              → 批次2（drama-production + QC闭环通用件）
              → 批次3（video-clips）→ 批次4（t2i-series + image-text-post）
              → 批次5（content-series）
```

- 总工期估计：9–13 人日（M0 1–2 人日，可与 M1 并行）；M2 按批次分批交付，
  **批次 1（约 4–5 人日）完成即达到 E2E-mock 基础链路可跑状态**（E2E-1/2/4/8/9/10），
  后续批次每批交付即解锁对应 E2E 用例（见 M3 标注）；
- 分支：deer-flow 用当前 `feat/gateway-media-api`；MGW 侧建议 `feat/auth-ticket`；
- 每阶段完成即在本文档勾选并附 PR 链接。

## 剩余工作清单（2026-11-01 晚盘点）

| # | 事项 | 状态 | 说明 |
|---|---|---|---|
| R1 | provider 真实网关联调（E2E-real 第一关） | ✅ 7 步全过（2026-11-01） | 真实生图 qwen-image-std 40s / 生视频 wan-t2v-std 50s / 幂等 / 隔离 404；发现并修复 provisioner `username→name` 契约 bug（9c7022ff）|
| R2 | 网关其它生图/视频 API 对接 | ✅ 1dcf8975（35 用例+194 回归绿） | compare/取消/评估→QC/全量档位/形状锁 |
| R3 | 生产环境部署 | ✅ 改为 prod-vm.sh 零镜像构建方案并已上线（2026 端口三层健康；00f06e9a） | 旧 deploy.sh 镜像方案退役保留 |
| R4 | E2E-real 逐功能验收 | 🔄 provider ✅（R1）→ **drama ✅（2026-11-02，见 [e2e-real-drama.md](e2e-real-drama.md)：冒烟 + skill E2E 全链路 + QC 归因通过；修复代理路由 404 bug 与 SKILL 必填字段缺口；遗留 i2v 网关侧 audio_vae bug、QC 失败→修正案例待补）**；console-link / video-clips / t2i/image-post / content-series ⏳ |
| R5 | E2E 报告 e2e-report.md | ⏳ R4 完成后 | 含 MGW 版本/env 指纹/用例结果 |
| R6 | deer-flow 验收 | 🔄 大头已完成（2026-11-01 晚） | 自动化：移动端对话生图 E2E-real **闭环通过**（五轮迭代修复后：登录→会话→qwen 真实生成→自动交付渲染全链路，断言 a/b/c 全过；经历 修复：langgraph rewrite、MGW 容器内可达、凭证中间件+JIT、CJK 字体、nginx 超时、自动交付保底）。人工：100.108.144.120:2027 持续 | 人工：100.108.144.120:2027（GLM/沙箱）；自动化：无头浏览器移动端 E2E（千问深圳月历 亮/暗）子代理执行中 |
| R7 | MGW 更新部署 | ✅ 已完成 | feat/auth-ticket 已合并回 feat/ref2va-multiref 并按原方案重新部署 |
| R8 | 遗留优化（非阻塞） | ⏳ | ① agent QC 自循环过长（7+ 次生成）——强化单轮约束或脚本侧硬上限 ② 生成中实时预览 ③ 移动端新会话 thread id 同步 ④ prod-vm status 容器内探测修 已完成(e4461906) ⑤ 后端进程意外终止观察（若复现查 OOM） |

### R2 明细：网关其它生图/视频 API 对接（images.md 契约）

- R2-1 `POST /v1/images/compare`（多 mode 对比生成）：provider/skill 暴露"对比模式"（一次提交 N 个 mode，响应按 mode 分组产物）
- R2-2 作业管理：`DELETE /v1/images/jobs/{id}`（取消/删除）接入 provider 与 media-jobs 语义（排队作业可取消）
- R2-3 评估读取：`GET /v1/images/jobs/{id}/assessment`（QC 评分）接入生成结果（quality 字段），供 drama/video-clips QC 闭环直接消费
- R2-4 多 mode 选择：确认 /v1/images/modes 全量档位（moss/qwen/wan/ltx/h3-*）经能力发现在模型下拉全部可选（含参考图类 ref2va 的参考图参数域）
- R2-5 形状锁：`GET/POST /v1/shape-lock` 状态提示接入生成前校验提示（仅提示不阻断）
