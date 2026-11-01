# 会话打开瓶颈剖析与查询优化方案

日期：2026-11-01
方法：真实无头浏览器（Playwright + Chromium CDP 弱网节流 500KB/s / RTT 100ms），目标会话 71bafe95（含大图产物长会话），对照组不限速；原始数据 `/tmp/prof/`。

## 一、实测结果

| 场景 | 打开→内容可见 | 说明 |
|---|---|---|
| dev :2027 弱网 | 15.8s | FCP 仅 0.8s——页面壳秒开，慢在数据 |
| dev :2027 不限速 | 9.4s | |
| prod :2026 不限速 | **9.1s** | 与 dev 几乎相同 → **瓶颈在后端，与网络无关** |

### 瓶颈排名

| # | 瓶颈 | 证据 | 归因 |
|---|---|---|---|
| 1 | `POST /threads/{id}/history` 服务端 **~13s**（主导） | 直调仍 12.9–13.1s；dev=prod 一致；响应仅 28–83KB 但 TTFB 8.3s | 服务端全量读取+反序列化 checkpoint，**持久层仍存 MB 级 base64**（response_sanitizer 只在 API 出口剥离） |
| 2 | 产物图 5.49MB | 2 张 PNG，弱网累计 20.8s（内容可见后继续加载） | 网络传输，图片缺懒加载/缩略图 |
| 3 | dev chunk 54 个 / 2.97MB | prod 预打包仅 1.18MB | dev 按需编译固有代价，非产品问题 |
| 4 | 消息全量挂载长任务 ~0.9s | 8 次 longtask（max 201ms） | 前端渲染，次要 |

### 已上线优化实效核对

| 项 | 实测 |
|---|---|
| ① 消息分页懒加载 | ✅ 生效：首屏 messages 仅 5 次请求共 1.6KB（limit=30） |
| ② base64 API 出清 | ✅ 生效：messages 单条 207B；❌ 持久层未动——正是 #1 根因 |
| ③ SSE gzip | ✅ 正常（本次无活跃流，静态验证 content-encoding: gzip） |
| ④ dev/prod 构建差异 | ✅ 吻合，且证明其非 9s 主因 |

## 二、当前存储架构（查询优化的基础）

- **数据库：SQLite（WAL 模式）单文件 `{data}/deerflow.db`**，dev 与 prod 相同；应用数据（threads/users/feedback 等，SQLAlchemy 异步）与 **LangGraph checkpointer（checkpoint blob 表）共用**。
- `DATABASE_URL` 未配置 → 未启用 Postgres；代码已支持 `postgres` 后端（`AsyncPostgresSaver` + 连接池，配置 `database.backend: postgres` 即可切换）。
- `/history` 查询路径：`threads.py get_thread_history` → checkpointer 按 thread 取**全部 checkpoint** → 反序列化 `channel_values`（含 base64 图片的完整消息 JSON）→ 组装响应。**13s 全部耗在 blob 读出 + JSON 反序列化**，网络传输只占零头。

## 三、查询优化方案

### A. history 查询修复【已完成 2256a903】

**真实根因（打点定位）**：不是"全量反序列化"，而是 `get_thread_history` 传给 `checkpointer.alist` 的 config **漏了 `checkpoint_ns`**——SQLite saver 的 WHERE 失去 namespace 谓词后，`ORDER BY checkpoint_id DESC` 退化为对全线程 751 行（3.6GB blob）的临时 B 树排序，读最新一行要 8.5s。**修复 = 一行：补 `checkpoint_ns: ""`**，查询走 PK 索引（10 行 3.1ms）。

效果：history API 13s → **16–33ms**；Playwright 实测打开 71bafe95 内容可见 **~15s → ~1.3s**（剩余为 dev 模式 JS chunk，prod 更快）。回归：新增 ns 钉住断言，threads 路由等 60 用例通过。

### A2. history 响应浅化（备选，未启用）
前端已走 `/runs/{rid}/messages?limit=30` 分页，`/history` 的全量 messages 大概率不再被消费。核实全部调用方（前端、IM 通道、subagents）后：history 响应的 messages 浅化为计数/最新 N 条/空表，跳过全量 blob 反序列化。**预期 13s → <1s。**

### B. 持久层 base64 出清（治本）
- **写入侧**：ViewImageMiddleware / 图片产物落盘到 outputs/（artifact URL），消息体只存引用，不再写 base64 进 checkpoint——存量不动，新会话即瘦。
- **存量**（可选，低优先）：一次性脚本把 checkpoint channel_values 内 `data:` base64 替换为文件引用（需抽帧验证 agent 回放不受影响），或干脆不迁移、随 SQLite→Postgres 迁移时清洗。

### C. checkpoint 查询本身优化（如 A+B 后仍慢再做）
- LangGraph checkpointer 表按 `thread_id` 查询有索引；热点在**单 checkpoint blob 过大**（MB 级 JSON 反序列化单点）。SQLite 下可用 `PRAGMA temp_store=MEMORY`、增大 `cache_size` 缓解 IO；但真正收益来自 A/B 减少反序列化量。
- 若上 Postgres：blob 存 TOAST，查询行为类似，A/B 仍是正解；Postgres 主要解决多节点并发写（SQLite WAL 单写者）。

### D. 图片传输（#2）
- 产物图懒加载（`loading=lazy` + 视口外不请求）+ 缩略图（首次返回小图，点开取原图）；代理路由可加 `Cache-Control: immutable`（artifact 内容不变）。

### E. 前端挂载（#4）
- 消息列表分批挂载/虚拟化（长任务 0.9s→可忽略）；优先级低于 A。

## 四、执行顺序

1. **A（checkpoint_ns 修复）** —— ✅ 完成（2256a903），打开 ~1.3s 达标
2. B 写入侧脱 base64 —— 降级为磁盘健康项（dev 库 8.1GB，两线程占 5.3GB；可结合 checkpoint_cleaner.py 做保留策略），非性能瓶颈
3. D 图片懒加载/缩略图 —— 弱网下仍有价值，未排期
4. C/E —— 已无必要（瓶颈消除）

## 五、数据库选型结论

当前 SQLite 够用（单机、WAL、读多写少）；打开慢**不是数据库引擎问题**，是查询形态问题（全量 blob 反序列化）。切 Postgres 的触发条件：多实例部署或 SQLite 写锁争用出现时，配置切 `backend: postgres` 即可，应用层无感。
