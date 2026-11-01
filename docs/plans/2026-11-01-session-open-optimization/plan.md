# 会话打开性能优化方案

日期：2026-11-01
背景：用户反馈打开长会话（如 71bafe95，含大图生成结果）非常慢。经量化定位，慢点不在 REST 接口（毫秒级），而在**传输体量**与 **dev 模式构建**：

| 实测项 | 量级 |
|---|---|
| SSE `runs/stream` 未压缩响应 | 28–98 MB/次 |
| 消息历史中的 base64 内联图片 | 单图 2–3 MB（全量随会话拉取） |
| dev 模式代码高亮（shiki）chunk | 打开一次会话拉取几十个 280B–7KB 的按需编译文件 |

## 问题清单与方案

### ① 会话历史全量加载 → 分页懒加载【进行中】

**问题**：打开会话时前端一次性拉取全部历史消息，长会话+含图消息体量巨大。

**方案**：
- 后端已支持 `limit` / `before_seq` 游标（`GET /api/threads/{id}/runs/{rid}/messages`），无需后端改动。
- 前端打开会话只取最近一页（约 30 条），滚动到顶部时按游标增量加载更早消息（锚定滚动位置防跳动），`has_more=false` 停止。
- 运行中会话的新消息仍走 SSE 实时追加，分页只作用于历史加载。

**验收**：长会话秒开；向上滚动可完整回溯历史；发送新消息流程不受影响。

### ② 消息 API base64 出清【进行中】

**问题**：ViewImageMiddleware / b64_json 将图片以内联 base64 存入消息，API 响应原样返回，单条消息达 MB 级。

**方案**（响应侧出清，持久层不动）：
- `image_url` 的 `data:image/...;base64,...` → 替换为 `image_url_removed` 降级标记（含原始字节数）。
- 任意字符串值中 >8KB 的 base64 数据段 → 截断为 `[base64 omitted N KB]`。
- `b64_json` 字段 → `b64_json_omitted: true`。
- 应用点：messages/events 只读端点 + SSE values 全量快照出口。
- 完整图片继续通过 `/api/media-gateway/file/{path}` 代理与 artifacts URL 按需获取。

**验收**：含图会话的 messages 响应无 base64；图片经 artifacts URL 正常展示（前端如依赖内联 base64 渲染，需后续把渲染切到 artifacts URL——影响面见子任务报告）。

### ③ SSE 压缩【已完成】

nginx `gzip_types` 加入 `text/event-stream`（dev 与 prod 模板均已加），28–98MB 的流降为约 1/10。

### ④ dev 模式 vs 生产构建差异【无需开发，说明项】

| | dev（`next dev`，:2027） | 生产（`next build`，:2026） |
|---|---|---|
| 代码形态 | 按需编译，每个模块单独响应 | 预打包为少数 minified bundle |
| shiki 高亮 | 每语言/主题独立小 chunk，现场拉取 | 预打进固定 bundle |
| 缓存 | 重启后文件名变化，缓存失效 | 内容哈希文件名，浏览器长期缓存 |

功能完全一致；dev 模式的慢是固有代价。对比验证：同一会话在 :2026 打开明显更快。

## 后续候选（未排期）

- 前端图片渲染统一走 artifacts URL（配合②，彻底移除内联 base64 的持久化写入——需动 ViewImageMiddleware，影响面更大）。
- StreamBridge values→增量快照（大改动，进一步减小 SSE 体量）。
- 代码块/图片懒渲染。
- 后端异常终止守护（OOM 检查，如复发）。

## 任务状态

| 任务 | 状态 |
|---|---|
| ① 前端分页懒加载 | 子代理执行中 |
| ② 后端 base64 出清 | 子代理执行中 |
| ③ SSE 压缩 | ✅ 已上线 dev+prod 模板 |
| ④ 说明项 | ✅ 本文档 |
