# 会话数据精简方案（Session Data Slimming）

- 日期：2026-11-03
- 分支：feat/gateway-media-api
- 状态：方案（仅设计，不含业务代码实施）
- 验收基准会话：`71bafe95-d0cb-4e65-90b2-2e912bd7fc9c`、`befa8ea8-*`、`47a3774b-*`

## 1. 背景与问题定位

### 1.1 现状（dev PG 实测，2026-11-03）

库：`deerflow` @ deer-flow-postgres（DATABASE_URL 指向 172.17.0.1:5432），`pg_database_size = 4134 MB`
（历史峰值 8.1GB，含 WAL/膨胀；prod 同容器族另 3148 MB，同量级问题）。

按表（含索引/TOAST，`pg_total_relation_size`）：

| 表 | 大小 |
| --- | --- |
| checkpoint_blobs | 2958 MB |
| checkpoint_writes | 1136 MB |
| checkpoints（元数据 jsonb） | 31 MB |
| runs / 其余 | < 1 MB |

checkpoint_blobs 按 channel：

| channel | 条数 | 大小 |
| --- | --- | --- |
| messages | 2684 | 2145 MB |
| __pregel_tasks | 1486 | 692 MB |
| viewed_images | 55 | 100 MB |
| 其余（thread_data 等） | — | ~55 MB |

### 1.2 根因

LangGraph 每次状态变化写全量 checkpoint：`messages` channel 是 AdditiveMessages channel，
每个 superstep 生成新 version，把**整条消息历史**（含 `ViewImageMiddleware` 注入的 base64
图片 HumanMessage）整体序列化为新 blob。一张 2–3MB 的图被复制进其后每个 checkpoint。

`ViewImageMiddleware`（`backend/packages/harness/deerflow/agents/middlewares/view_image_middleware.py`）
在 `view_image` 工具完成后注入一条 `hide_from_ui` HumanMessage，content 为
`[{type:text}, {type:image_url, image_url:{url:"data:<mime>;base64,..."}}...]`。
`view_image` 工具无独立 vision model 时把 base64 写进 `viewed_images` state（55 条 blob 共 100 MB）。

### 1.3 实测量化（风险验证 #2）

对 `71bafe95`（重度会话）逐 blob 扫描 base64 长串（≥4000 字符）：

- 504 条 blobs 共 1.24 GB，其中 base64 占 **1.21 GB（97.7%）**
- 最新一条 messages blob = **7,603,112 bytes**，json 化 7.63MB，其中 base64 **7,536,176 bytes（99.1%）**
- 形态：**1 条 HumanMessage（`view_image_context`，hide_from_ui）内含 3 个 `image_url` data-URL part**
  （`data:image` 出现 3 次，每张 ~2.5MB）——即 tool-result 不携带图片，base64 全部集中在中间件注入的
  单条 human 消息里
- 该会话 1206 个 checkpoint、checkpoint_writes 467 MB
- `befa8ea8` / `47a3774b` 两个会话 blobs 内**无 base64**（它们是中断/重试型会话，膨胀来自 checkpoint 数量而非图片）
- 全库扫描：messages channel 中 >200KB blob 有 408 条共 **2.10 GB**（几乎全部可替换）；
  checkpoint_writes 中 >200KB blob 225 条共 **1.11 GB**（pre-agent state 写入同样携带注入图片消息）；
  `__pregel_tasks` channel 725 MB（task 内嵌 pending writes，同样含 messages 副本）

### 1.4 风险验证 #1：base64 是否会重发给模型

**结论：会重发。** 构造路径：`create_agent`（langchain）直接以 `state["messages"]` 全量历史作为
模型输入；`SummarizationMiddleware`（`DeerFlowSummarizationMiddleware`）只在 token 阈值触发时才把
旧消息折叠成 summary，阈值内历史——含注入的 image_url data-URL——每次 LLM 调用都会原样重发。
这同时意味着：

1. **token 成本**：重度会话每一轮都重复付费 7MB 级的图片输入（同时也是首字延迟主因之一）；
2. **替换的语义影响**：源头替换后，旧轮次的图片模型"看不见"了。评估：
   - 这些图片的原始文件**都在磁盘上**（`/mnt/user-data/{workspace,uploads,outputs}/...`，view_image
     只允许这三个虚拟根，工具入参即虚拟路径）；
   - 中间件的注入消息里每个 image part 前都有 `- **{image_path}** ({mime})` 文本行，**替换后路径信息仍在**；
   - 模型需要回看旧图时可重新调用 `view_image`（工具仍然工作，中间件会重新注入新 base64）；
   - 结论：**用"占位文本 + 虚拟路径"替换是可接受的降级**，非零风险但可控（见 §4 回归清单）。
   - 可选增强（决策点 D6）：为保留语义，可只对"已过注入窗口"的消息替换，最近一条注入消息保留 base64。

### 1.5 现有相关机制盘点

| 机制 | 位置 | 现状 |
| --- | --- | --- |
| response_sanitizer | `backend/app/gateway/response_sanitizer.py` | API 出口把 data-URL/>阈值 base64 替换为占位符；**只治出口不治存储** |
| serialize slim | `backend/packages/harness/deerflow/runtime/serialization.py`（`_slim_message` / `strip_data_url_image_blocks`） | SSE values / history 出口瘦身，同样不落库 |
| subagent JSONL sanitize | `backend/packages/harness/deerflow/subagents/session.py` | 子代理 JSONL 已做 base64 剥离（对应脚本 `sanitize_session_jsonl_base64.py`），**主 agent checkpoint 未覆盖** |
| CheckpointCleaner | `backend/app/gateway/checkpoint_cleaner.py` | **SQLite-only**：`_get_aiosqlite_conn` 取 `checkpointer.conn`，Postgres saver 是 `pool` 属性 → 返回 None → **Postgres 模式下静默跳过（现状 no-op）** |
| `prune_checkpoints.py` | `backend/scripts/` | 保留 N 条 + orphan 清理，**SQLite-only** |
| `cleanup_thread_state.py` | `backend/scripts/` | 用 saver API（alist→改写 channel_values→aput）清理**仅最新 checkpoint**，旧 checkpoint 的 blob 不动 |
| 中断保留收集器 | `db5e768c`：worker `_StreamedMessageCollector` 在 abort 时 `aput_writes` 提交 partial reply | 见 §3.5 兼容性 |
| 读侧合并 | `threads.py:_merge_pending_messages` | 按 message id 去重合并 pending messages（见 §3.5） |

## 2. 方案总览

三层组合，按分期落地：

- **P1 写入侧替换**（根因止血）：checkpoint 落库前把 base64 换成文件引用，新数据即瘦；
- **P2 保留策略**：每线程保留最近 N 个 checkpoint + 锚点，定时任务（修 Postgres 支持）；
- **P3 存量清洗**：一次性幂等脚本清洗历史 checkpoint（引用替换）+（可选）保留策略套用。

预期收益（按 §1.3 实测）：
- P1 后：新增 checkpoint 的 messages/viewed_images/pending-writes blob 缩小 **~95–99%**；
- P2 后：每线程 checkpoint 数上限化，dev 库预计降到 **< 1 GB**；
- P3 后：全库存量清完，预计 **4.1 GB → 约 0.6–1.0 GB**（`__pregel_tasks` 中非图片部分
  （task 结构、非图消息）保留；`checkpoints` 元数据表 31MB 不变）。

## 3. P1：写入侧替换

### 3.1 替换点（唯一 choke point）

不逐个改调用方，而是**包一层 checkpointer 装饰器**：
`SlimmingCheckpointer`（组合/继承 `AsyncPostgresSaver`，或实现 `BaseCheckpointSaver` 接口包 delegate）：

- `aput(config, checkpoint, metadata, new_versions)`：对将要写出的 channel_values 做替换后再序列化落库；
- `aput_writes(config, writes, task_id)`：对 writes 里的 messages/viewed_images 值做同样替换
  （覆盖 worker 的 `_StreamedMessageCollector` 提交路径与 LangGraph `__interrupt__` 路径）；
- `alist / aload_typed / aget_tuple / adelete_thread / adelete_for_runs`：原样透传。

装配点：`deerflow/runtime/checkpointer/async_provider.py::make_checkpointer` 返回前包一层；
sqlite/memory 后端同样包装（行为一致，测试环境可验证）。开关：config `checkpoint_slimming.enabled`（默认开），
替换策略可配（见 §6 决策点）。

### 3.2 替换规则

对 messages channel 与 checkpoint_writes 中的值（message 对象或 dict），遍历 content parts：

- `{type:"image_url", image_url:{url:"data:<mime>;base64,<payload>"}}`
  → `{type:"text", text:"[图片已存档：<virtual_path> (<mime>, ~<n>KB)；需要查看请调用 view_image]"}`；
- 对 `viewed_images` channel：`{"<path>": {"base64": ..., "mime_type": ...}}`
  → `{"<path>": {"mime_type": ..., "file_ref": "<virtual_path>", "base64": null}}`（保留 mime/path，删 payload）。

**virtual_path 的获取（base64 来源清单与落盘方案）**：

| 来源 | 是否已在磁盘 | 落盘/引用方案 |
| --- | --- | --- |
| `view_image` 读 workspace/outputs/uploads 下文件（主来源，100% 场景） | 已在磁盘 | 直接用注入消息文本中的虚拟路径；替换器解析同一 HumanMessage 里 `**<path>**` 文本行与 image part 的顺序配对 |
| 用户上传图片（uploads router → 用户 uploads 目录） | 已在磁盘（上传即落盘，前端消息只带 metadata） | 无需处理；若未来前端改为发 base64 内联消息，则替换器把 payload 写入 `<user-data>/uploads/inline/<thread>/<msg-id>-<n>.png` 再引用（列为防御性 TODO，不进 P1） |
| 工具动态生成但未落盘的图（当前不存在：media_gateway 工具只写 outputs 后返回路径，grep 证实无 base64 进 ToolMessage） | — | 保持约定：**新增工具禁止把 base64 塞进消息**，替换器兜底把无路径 payload 写入 `<user-data>/outputs/slimmed/<thread>/<checkpoint-id>/<idx>.<ext>`（兜底逻辑必须有，路径可从 message id 推导） |

### 3.3 对出口链路的影响（兼容性确认）

- **response_sanitizer**：源头替换后 API 出口不再有 data-URL，`sanitize_value` 的两个正则自然
  空转；逻辑保留作为兜底（防御未包装路径/旧数据），**无需改动**。
- **serialize slim（`_slim_message` / `strip_data_url_image_blocks`）**：替换后的文本 part 正常走
  文本截断逻辑，行为不变。
- **前端渲染**：注入消息本就 `hide_from_ui`，前端从不渲染这些图；产物图渲染走 outputs 文件 URL，
  与 checkpoint 内容无关。
- **`_merge_pending_messages`（读侧合并，db5e768c b 侧）**：合并按 message id 去重，替换不改 id，
  两侧（committed 已替换 / pending 也经 `aput_writes` 替换）一致，合并结果仍为瘦消息。兼容。
- **中断保留收集器（db5e768c a 侧 `_StreamedMessageCollector`）**：它提交的是 AIMessageChunk
  （文本流），不含 base64；即使未来含图，`aput_writes` 包装层统一替换，行为一致。兼容。

### 3.4 验收标准（P1）

1. 新重度会话（view_image ≥5 张）结束后，其最新 messages blob < 1MB（对照基线 7.6MB）；
2. `checkpoint_blobs` 新增量（按 `version` 时间窗）低于替换前的 5%；
3. 模型行为：新会话中 view_image 后当轮模型仍能"看见"图（中间件注入发生在 checkpoint 之前、
   模型调用之后——替换只影响落库副本，**当轮内存中的消息不受影响**，这是本方案关键安全性）；
   次轮续聊模型可正常引用图路径并可用 view_image 重读；
4. /history、/state、SSE values 打开 `71bafe95` 正常（旧数据仍由出口 sanitizer 兜底）。

## 4. P2：保留策略（每线程 N + 锚点）

### 4.1 策略

- 每线程每 checkpoint_ns 保留**最近 N 个 checkpoint**（沿 parent 链从叶子回溯，复用
  `checkpoint_cleaner.py` 现有 keep-set 算法），N 默认 **20**（决策点 D3）；
- **锚点（anchor）**：每线程额外保留每个"里程碑" checkpoint——metadata 带
  `interrupt` / 人工 resume / summarization 发生点的 checkpoint——避免误删分支分叉点导致
  时间旅行/重放不可用；实现上先只保留"每个分支的叶子 + 最近 N"（现有算法已保证叶子不删），
  锚点识别作为增强项；
- checkpoint_writes 随其所属 checkpoint 一起删（保留叶子 checkpoint 的 pending writes，db5e768c 依赖）；
- 孤儿线程（threads_meta 无记录）整删，沿用 `prune_checkpoints.py --remove-orphans` 语义。

### 4.2 定时任务接入（checkpoint_cleaner.py 评估：**需改**）

现状对 Postgres 是 no-op（§1.5）。改动最小方案：保留其 `threading.Timer` 调度骨架与
日志/dry-run 结构，把 `_cleanup_all` 的 SQL 层抽成两个后端实现（aiosqlite 直连 / psycopg pool），
Postgres 端通过 `checkpointer.pool` 执行与 saver 相同语义的 DELETE
（keep-set 计算优先走 saver API：`alist` 列全 checkpoint + parent 链，避免裸 SQL 理解 schema 的漂移风险）。
**保持"叶子永不删"双保险校验。** 运行于 Gateway lifespan（现有位置，app.py:275-288）。

验收标准（P2）：开 N=20 跑一轮后，`71bafe95`（1206 ckpt）降到 ≤ 20+锚点，线程相关 blobs 缩到
 tens-of-MB 级；`befa8ea8` 的 pending writes（中断恢复语义）完好，刷新后回复仍可见。

## 5. P3：存量清洗脚本

`backend/scripts/slim_checkpoints_postgres.py`（新脚本，一次性 + 可重复跑）：

### 5.1 实现原则

- **走 saver API，不裸 UPDATE blob**：`async with make_checkpointer() as cp` → 按线程
  `cp.alist` 逐 checkpoint `aload_typed`（serde.loads_typed 等价路径）→ 反序列化 channel_values →
  §3.2 替换规则改写 → **`cp.adelete_for_runs`/底层不可用则用"同 version 重写"**：Postgres saver 的
  blob 按 `(thread_id, ns, channel, version)` 主键 upsert，故对同一 version 重新 put 覆盖 blob
  （教训来源 `migrate_sqlite_to_postgres.py`：裸 SQL 复制曾在 serde 版本差异上翻车；saver API
  保证 serde 一致）。若某 version 无法覆盖，则退化为"只清洗最新 K 个 checkpoint + 依赖 P2 删旧"。
- **幂等**：替换前检测——消息内无 >4KB base64 串即跳过（同 `cleanup_thread_state.py` 的 scan 逻辑）；
  重复执行零副作用。
- **可中断**：按线程为最小事务单位（见 5.2）；启动时记录/恢复 `--resume-state <json>`（已处理线程清单），
  Ctrl-C/崩溃后重跑自动跳过已完成线程。
- **范围过滤**：`--threads <prefix,...>`、`--min-size <KB>`（默认只处理含 >200KB blob 的线程，
  即实测约 5–8 个线程）、`--dry-run`、`--backup`。
- **删旧 blob 的注意点**：messages channel 的 version 链被 P2 删掉一部分后，部分历史 version 的
  blob 已无 checkpoint 引用（`checkpoint_blobs` 无外键），清洗脚本附带 `--purge-unreferenced`：
  以 `checkpoints.checkpoint` jsonb 中记录的 channel_versions 为准，删除无引用的 (channel, version) 行。

### 5.2 事务与安全

- 线程级事务：同一线程的多个 version 重写放在一个 psycopg 事务里（`pool.connection()`），
  失败整体回滚该线程，不碰其他线程；
- 失败中断：任何异常 → 该线程记入 failed 清单、继续下一线程；结束时打印汇总（线程数、字节数、失败清单）；
- 运行窗口：要求低峰期执行且**先停 Gateway**（或至少确认无活跃 run），避免清洗与写入竞争同一 version。

### 5.3 备份方案

执行前全库备份（dev/prod 同样流程）：

```bash
docker exec deer-flow-postgres pg_dump -U deerflow -Fc -d deerflow \
  -f /var/lib/postgresql/data/backup/pre-slim-$(date +%Y%m%d-%H%M).dump
docker cp deer-flow-postgres:/var/lib/postgresql/data/backup/pre-slim-*.dump \
  ~/backups/deerflow/
```

回滚：`docker exec -i deer-flow-postgres pg_restore -U deerflow -d deerflow --clean --if-exists <dump>`。
另可在清洗前 `CREATE TABLE checkpoint_blobs_bak AS SELECT * FROM checkpoint_blobs` 做表级快照
（约 3GB，清洗验证通过后删），支持只回滚该表。

### 5.4 收益测算（按 §1.3 实测）

| 项目 | 现值 | 清洗后 |
| --- | --- | --- |
| messages >200KB blobs（408 条） | 2.10 GB | ~20–100 MB（保留文本） |
| writes >200KB blobs（225 条） | 1.11 GB | ~50 MB |
| __pregel_tasks | 0.73 GB | ~0.3–0.5 GB（非图部分保留；P2 删旧 checkpoint 后进一步降） |
| viewed_images | 0.10 GB | ~0 |
| **合计** | **~4.0 GB** | **~0.6–1.0 GB**（叠加 P2 后下探至 ~0.3 GB） |

验收标准（P3）：`pg_database_size` < 1.2 GB；三个基准会话通过 §6 回归清单；`--dry-run` 输出与
实跑字节数一致（±5%）。

## 6. 回滚与安全（贯穿 P1–P3）

- **dry-run**：P3 脚本与 P2 cleaner 均支持 `--dry-run`（打印将删/将改的 thread、checkpoint 数、
  预估字节），先跑 dry-run 出报告给用户确认；
- **失败不破坏**：§5.2 线程级事务；P1 装饰器在替换函数抛异常时**降级为不替换原样落库**
  （宁可膨胀不可丢数据）并打 error 日志；
- **回归清单**（三个基准会话逐一执行，P1/P2/P3 各跑一遍）：
  1. 打开会话：/history 返回完整消息序（含 hide_from_ui 消息的文本部分）；
  2. 续聊：模型正常响应，能引用历史产物路径；涉及旧图的问题验证 view_image 重读可用；
  3. 中断恢复语义（befa8ea8 场景）：制造 abort，刷新后 partial reply 仍可见、不重复；
  4. 产物渲染：outputs 下生成的图片/视频在 UI 正常显示（文件渲染不依赖 checkpoint）；
  5. 子代理任务列表与 `__pregel_tasks` 相关 resume 正常；
  6. 管理端跨 owner 线程读取（G5 语义）不受影响；
- **回滚开关**：config `checkpoint_slimming.enabled: false` 一键停用 P1（重启生效）；P2/P3 是
  数据删除/改写，只能靠备份回滚 → 备份保留期 ≥ 30 天。

## 7. 实施分期与工作量

| 期 | 内容 | 工作量 | 验收 |
| --- | --- | --- | --- |
| **P1** | 风险验证（本文件 §1.3/§1.4 已完成）；SlimmingCheckpointer 装饰器 + 替换器（含 viewed_images、aput_writes、兜底落盘）；config 开关；单测（替换器纯函数 + 装饰器集成 + 降级路径） | **M**（1–2 天） | §3.4 |
| **P2** | checkpoint_cleaner Postgres 后端改造 + 锚点/keep-set 复核；N 可配 + 默认 20；单测（keep-set、叶子保护、pending writes 保留） | **S**（0.5–1 天） | §4.2 |
| **P3** | slim_checkpoints_postgres.py（dry-run/resume/线程事务/purge-unreferenced）；备份 SOP；跑三个基准会话回归 | **M**（1–2 天，含生产执行窗口） | §5.4 + §6 回归 |

依赖关系：P1 独立；P2 独立（可与 P1 并行）；P3 依赖 P1 的替换器复用（同一段替换代码）。
约束遵守：不触碰 media_gateway providers 提交链（所有改动集中在 checkpointer 装饰层、cleaner、scripts）。

## 8. 决策点清单（需拍板）

| # | 决策点 | 选项 | 建议 |
| --- | --- | --- | --- |
| D1 | 引用格式 | (a) 纯文本占位含虚拟路径；(b) 结构化 `file_ref` part（自定义 content block） | **(a)**——零 schema 风险，所有出口/模型端天然兼容；(b) 需要改序列化与前端 |
| D2 | 孤儿文件清理 | 清洗时是否连 outputs 里未被 checkpoint 引用的产物文件一起清 | **不清**——outputs 是产物交付物，删除影响用户资产；仅提供独立报告脚本列出疑似孤儿 |
| D3 | N 默认值 | 10 / 20 / 50 / 100 | **20**（时间旅行/重放仍有足够回溯；71bafe95 场景下每线程 < 50MB） |
| D4 | 旧图语义 | (a) 全部替换为占位；(b) 最近一条注入消息保留 base64，更旧的替换 | **(a)** P1 先全替换（当轮内存不受影响，语义损失仅在跨轮回看旧图，view_image 可重读）；若回归发现模型频繁需要跨轮旧图再升级为 (b) |
| D5 | P3 清洗时是否同时套用 P2 保留策略（删旧 checkpoint） | 只替换 / 替换+删旧 | **替换+删旧一次到位**（备份在手，删旧是收益大头），但以 --keep 参数显式控制 |
| D6 | sqlite/memory 后端是否同样启用替换 | 只 Postgres / 全后端 | **全后端**——同一代码路径，测试环境可覆盖 |
| D7 | prod 库（3.1GB）执行时间窗 | 立即 / 与 dev 一起 / 单独排期 | 由用户定，脚本就绪后单独排期 |

## 9. 涉及文件清单（实施时）

- `backend/packages/harness/deerflow/runtime/checkpointer/async_provider.py` — 装饰器装配
- `backend/packages/harness/deerflow/runtime/checkpointer/slimming.py`（新增）— 替换器 + 装饰器
- `backend/app/gateway/checkpoint_cleaner.py` — Postgres 后端
- `backend/scripts/slim_checkpoints_postgres.py`（新增）— P3 清洗
- `config.example.yaml` — `checkpoint_slimming` / `checkpoint_cleanup` 配置示例
- 测试：`backend/tests/test_checkpoint_slimming.py`、`test_checkpoint_cleaner_postgres.py`（新增）
- 文档：backend/AGENTS.md（persistence 节）与 README 更新（同 change set）
