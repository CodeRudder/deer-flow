# feat/gateway-media-api 变更清单与 PR 拆分方案

日期：2026-11-02
范围：`main..feat/gateway-media-api` 共 59 个提交（`git log --oneline main..HEAD`）。
用途：后续合并 main 或按工作流拆分独立 PR 的依据。每节列出提交、内容摘要、依赖关系与验证状态。

## PR-1 集成设计文档（可先行，无代码依赖）

| 提交 | 内容 |
|---|---|
| be87b56c | Media Gateway 集成设计/工作流/验收/E2E 四件套 |
| 5ff402b6 | 模型中立 provider 复用 + 短剧/视频片段 skill 设计 |
| c9a89dba | 剧本编写-分镜五要素-检验修正闭环设计 |
| cb503613 | 内容创作矩阵（组图/图文帖/短剧/视频）设计 |
| cc7f4024 / d56ca6c6 / 07f442bb | M2 批次重排 / 剩余工作清单 / 逐功能上线策略 |
| a3cfa2f6 | E2E 分 mock/real 两级 |
| 706e5521 | M0 网关 ticket 改造范围界定 |
| 4f0d1b07 / febbd4ad | E2E-real 收口 / R1-R3 进度记录 |
| e4f0ac9c | §5.4 凭证注入方案 |
| 35fe1d47 / f00b5af0 / 1671d55e / a2fc6213 | 插话研究与 Plan B（纯文档） |

## PR-2 MGW 集成核心（M1+M2，依赖 PR-1 文档无强依赖）

- `f2978399` M1 基础通道：MGW client、JIT provisioner、映射表
- `d070e977` M1 修复：契约对齐的用户 claim + 生命周期钩子
- `545ca12a` 进程内 MGW mock server（E2E-mock 层）
- `6a430ae8` 产物代理路由 + console-link API/skill
- `d63129bb` 生图/生视频 provider 注册（media_gateway）
- `679836ef` provider 适配真实 MGW images 契约
- `030cad5e` / `60cff0e9` / `7cd2f1e5` / `cdc4b15e` / `05a9c276` 五大编排 skill：video-clips / drama-production / t2i-series / image-text-post / content-series
- `d5fd19fb` E2E-mock 旅程测试
- `97685690` + `e2eb4a0d` + `143b7e7f` 沙箱 per-user 凭证注入链 + 修复
- `184998ff` ArtifactAutoPresentMiddleware（run 结束自动产物登记）
- `9c7022ff` provisioner 按真实契约用 name 字段
- 验证：MGW 相关 306 用例 + E2E-mock 全绿；E2E-real 生图链路 5 轮闭环通过

## PR-3 模型注册表与归并（依赖 PR-2）

- `c43dd6d0` 全量枚举网关档位进 registry（TTL 缓存、失败回落）
- `8cd7588b` `*2va` 档补进生视频列表
- `214f6125` 同引擎族归并（23 档→生图 5/生视频 8，quality 参数分档 std/hq/ultra/2k，h3-t2v/h3-i2v/h3-ref 命名）
- 验证：枚举/归并/quality 解析单测全绿，真实网关实证

## PR-4 会话性能优化（独立可先行）

- `2f7c24a3` + `9637dbf4` nginx gzip 含 text/event-stream（dev+prod 模板）
- `0dde3acb` + `05370070` dev nginx 路由修复（langgraph rewrite、threads 超时）——如按"部署"拆可移入 PR-5
- `4625555a` 消息 API base64 响应出清（response_sanitizer，SSE 统一出口）
- `84854673` 前端历史分页懒加载（limit=30 首屏 + 滚动增量，含 V2 分支修复）
- `2256a903` /history 补 checkpoint_ns 谓词（13s→30ms，打开 9.1s→0.6s）
- `6549994b` 产物图 720p JPEG 变体 + 原图下载链接（2.97MB→41.7KB，磁盘缓存 immutable）
- 实测：Playwright 弱网/不限速复测报告 docs/plans/2026-11-01-session-open-optimization/

## PR-5 部署与运维（独立可先行）

- `5fc46c03` + `9df63b46` + `35ed3fff` dev-vm 持久容器方案 + nginx 前置
- `7f9cb4f5` + `dbd4147f` + `00f06e9a` 独立部署目录 → prod-vm 零镜像方案
- `e4461906` prod-vm status 探测修复
- `a8253be1` prod-vm postgres 感知 + 连接串防覆盖
- `c17ca379` SQLite→Postgres 迁移工具（checkpoint 经 saver API 转换，幂等）
- `2506195d` dev nginx 长任务超时（与 skill 相关，可归 PR-2）

## 建议合并顺序

PR-1（文档）→ PR-5（部署基建）→ PR-2（集成核心）→ PR-3（模型归并）→ PR-4（性能）。若一次性合并整分支也成立：全部提交已在 dev/prod 环境实测（Postgres 双环境、弱网复测、真实网关联调）。

## 遗留/待提交项

- 中断 run 最后消息丢失修复（进行中，完成后补入 PR-4）
- `prod-vm.sh restart` stop/up 竞态（已知小缺陷，重启后需手动确认 gateway）
- docs/plans/2026-11-01-session-open-optimization/ 与本文件尚未 git add
