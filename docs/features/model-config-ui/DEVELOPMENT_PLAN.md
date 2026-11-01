# 模型配置管理界面 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Web 设置界面增删改大模型配置，精准写回 `config.yaml`，保存后无需重启即可生效。

**Architecture:** 后端新增一个**行级外科编辑器**（不整体序列化 YAML），只重写 `models:` 段内被标记符圈定的区域，其余字节原样保留。写前在内存中校验，失败即回滚。前端在设置弹窗新增「模型」分区，复用 `agents.py` / `mcp.py` 已有的 `require_admin_user` 管理模式。

**Tech Stack:** Python 3.12 + FastAPI + Pydantic + PyYAML（仅解析不写回）；Next.js 16 + React Query + shadcn/ui。

---

## 一、五个决定性约束

这些是实测确认的，不是假设。每条都直接约束实现方式。

### 约束 1：绝不整体 `yaml.dump`

`config.yaml` 共 **1821 行，其中 1471 行是注释（80%）**，写了大量"为什么这样配"的说明。

```python
# 这样做会抹掉 1471 行注释：
yaml.dump(config_dict, f)   # ← 禁止
```

**对策**：把 `config.yaml` 当**文本**处理，而不是当数据结构。只做行级替换。

### 约束 2：连 `models:` 段内的模板注释也不能丢

`models:` 段（L76–L573，498 行）目前**全是注释**——那是给用户参考的示例模板。整体替换该段会连带删掉这些示例，属于"影响其他注释"。

**对策**：用**标记符圈出托管区域**，只重写标记之间。模板注释原样留在标记之外。

首次写入前：

```yaml
models:
  # Example: Volcengine (Doubao) model
  # - name: doubao-seed-1.8
  #   ...
  # Example: OpenAI model
  # ...
```

首次写入后（标记之间可整体重写，标记之外一字不动）：

```yaml
models:
  # >>> DeerFlow Web UI 托管区域 — 界面会整体重写，请勿手工编辑 >>>
  - name: doubao-seed-1.8
    display_name: Doubao-Seed-1.8
    use: deerflow.models.patched_deepseek:PatchedChatDeepSeek
    model: doubao-seed-1-8-251228
    api_base: https://ark.cn-beijing.volces.com/api/v3
    api_key: $VOLCENGINE_API_KEY
    timeout: 600.0
    supports_thinking: true
  # <<< DeerFlow Web UI 托管区域结束 <<<
  # Example: Volcengine (Doubao) model     ← 模板注释保留
  # - name: doubao-seed-1.8
  #   ...
```

> 取舍说明：标记符区域外的**手工** `models` 条目界面看不到也管不着。
> 这是刻意的——不碰用户手写的内容。文档里要写明这一点。

### 约束 3：坏配置会让整个服务挂掉（最高风险）

`get_app_config()` 的重载路径**没有异常保护**：

```python
# backend/packages/harness/deerflow/config/app_config.py:606
if should_reload:
    ...
    _load_and_cache_app_config(str(resolved_path))   # ← 抛异常直接冒到每个请求
```

而 `resolve_env_variables` 对解不出的 `$VAR` 是**硬失败**：

```python
# backend/packages/harness/deerflow/config/app_config.py:403-405
if env_value is None:
    raise ValueError(f"Environment variable {config[1:]} not found for config value {config}")
```

**对策**：写入流程必须严格**五步——副本 → 改副本 → 校验副本 → 备份原件 → 替换**：

```
1. 复制 config.yaml → config.yaml.work          （不动原件）
2. 在 config.yaml.work 上做外科替换              （改的是副本）
3. 校验 config.yaml.work：AppConfig 解析 + $VAR 可解析 + 条目合法
   └─ 失败 → 删除副本，中止。原件自始至终未被触碰
4. 备份：复制 config.yaml → config.yaml.bak.<时间戳>
   └─ 备份失败（磁盘满/权限）→ 中止，不替换
5. os.replace(config.yaml.work, config.yaml)     （原子替换）
```

**为什么必须先备份再替换**：`os.replace` 是原子的，覆盖瞬间完成、没有中间态。
校验只能发现*静态*问题（YAML 语法、`$VAR` 缺失、字段类型），
发现不了*运行时*问题——比如某个 provider 字段只在真正发起请求时才报错。
没有备份的话，这类问题一旦暴露，原文件已经被覆盖，没有退路。

**备份用带时间戳的文件名**，不用固定的 `config.yaml.bak`：
固定名在第二次编辑时会被覆盖，于是"编辑 1 引入了隐藏问题 → 编辑 2 正常执行"
之后，备份里存的已经是带问题的状态，原始版本永久丢失。时间戳保留历史，
并按保留策略（默认最近 10 份）清理，避免无限增长。

### 约束 4：`load_dotenv()` 只在导入时执行一次

```python
# backend/packages/harness/deerflow/config/app_config.py:44（模块级）
load_dotenv()
```

往 `.env` 写完新 key 后，**运行中的进程读不到**——`os.environ` 不会自动更新。于是 `$新KEY` 解析为 `None` → 触发约束 3 的硬失败。

**对策**：保存时同时做三件事：写 `.env`、**在进程内 `os.environ[key] = value`**、写 `config.yaml` 的 `$VAR` 引用。

> `load_dotenv()` 默认不覆盖已存在的环境变量，所以重启后从 `.env` 加载的值与内存一致，不会打架。

### 约束 5：密钥绝不能回传给浏览器

现有 `ModelResponse`（`models.py:14`）**刻意比 `ModelConfig` 窄**，就是为了不外泄凭据。

**对策**：管理接口返回 `api_key` 时一律**掩码**（`sk-****1234`）。编辑时若用户不重新输入，保留原值不覆盖。

---

## 二、文件结构

| 文件 | 职责 |
|---|---|
| `backend/packages/harness/deerflow/config/models_section.py` | **新增**。纯函数外科编辑器：定位托管区、生成 YAML 片段、替换。无副作用，易单测 |
| `backend/packages/harness/deerflow/config/model_config.py` | 复用现有 `ModelConfig`（`extra="allow"`，能容纳 `api_base` 等 provider 字段） |
| `backend/app/gateway/routers/models.py` | **扩展**。新增管理员专属的读写与连通性测试端点 |
| `backend/app/gateway/deps.py` | 复用 `require_admin_user`（:410） |
| `backend/tests/test_models_section.py` | **已完成**（Task 1–3，1637 行）。编辑器 / 校验 / 提交 / .env 单测 |
| `backend/tests/test_models_config_api.py` | **新增**。端点契约 + 权限 + 回滚测试 |

> ⚠️ **测试文件不再合并（执行中调整）**
>
> Task 1–3 的测试全堆在 `test_models_section.py`，导致该文件涨到 **1637 行 / 110 个测试**。
> 子代理每接一个 Task 都要先全量读它 + 724 行实现 = **2361 行**，上下文被吃光，
> 实测连续多次超时。
>
> **从 Task 4 起：每个 Task 独立测试文件**，不再往 `test_models_section.py` 追加。
> 已完成的 Task 1–3 **不重构**——它们已验证通过，改动是白担风险。
| `frontend/src/core/models/api.ts` / `hooks.ts` / `types.ts` | **扩展**。管理接口的客户端与 React Query hook |
| `frontend/src/components/workspace/settings/model-settings-page.tsx` | **新增**。设置界面分区 |
| `frontend/src/components/workspace/settings/settings-dialog.tsx` | **修改**。注册新分区 |
| `frontend/src/core/i18n/locales/{zh-CN,en-US,types}.ts` | **修改**。三处同步加文案 |
| `.gitignore` | **修改**。已有 `config.yaml.bak`；需补 `config.yaml.bak.*`（时间戳备份）与 `config.yaml.work` |
| `docs/windows-deployment/operations.md` | **修改**。补充界面配置模型的说明与备份/回滚操作 |

---

## 三、任务分解

### Task 1：托管区外科编辑器（纯函数）

**Files:** Create `backend/packages/harness/deerflow/config/models_section.py`；Test `backend/tests/test_models_section.py`

**核心接口**（函数签名，供后续任务依赖）：

```python
MANAGED_BEGIN = "# >>> DeerFlow Web UI 托管区域 — 界面会整体重写，请勿手工编辑 >>>"
MANAGED_END   = "# <<< DeerFlow Web UI 托管区域结束 <<<"

def find_models_block(lines: list[str]) -> tuple[int, int] | None:
    """返回 models: 段的行区间 [start, end)，找不到返回 None。"""

def render_managed_section(models: list[dict]) -> list[str]:
    """把模型配置列表渲染成托管区行（含标记符，缩进 2 空格）。"""

def replace_managed_section(text: str, models: list[dict]) -> str:
    """把 text 中托管区替换为 models；无标记则在 models: 行后插入。"""
```

- [ ] **Step 1：写失败测试**——覆盖这些用例（每个用例都是实测需求，不是凑数）：
  - 标记符不存在时，插入到 `models:` 行**正下方**、模板注释**之上**
  - 标记符存在时，只替换两标记之间
  - **替换前后，标记区以外的每一行必须逐字节相同**（用 `splitlines()` 比对，这是本任务最核心的断言）
  - `models` 为空列表 → 托管区渲染成空，但标记符保留
  - `models:` 段是文件最后一个顶层键（无后续 `^[a-z_]+:` 行）→ 边界取到文件尾
  - 文件含 CRLF → 行尾风格在输出中保持
  - 缩进 2 空格，与 `config.example.yaml` 一致
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：实现**——纯行操作，`str.splitlines(keepends=True)` 保行尾
- [ ] **Step 4：跑测试确认通过**
- [ ] **Step 5：提交** `feat(config): add surgical models-section editor`

**验收**：`pytest tests/test_models_section.py -v` 全绿；对真实 `config.example.yaml` 跑一次替换，`diff` 显示**只有托管区**新增行。

> **实测补记（首轮评审发现并修复）**：`models:` 行**带行内值**时插入托管区会产生**非法 YAML**，
> 而本项目的配置重载没有异常保护 → 等于服务全挂。实测四种输入：
>
> | 输入 | 结果 |
> |---|---|
> | `models: []` | ❌ 非法 YAML |
> | `models: null` | ❌ 非法 YAML |
> | `models: []  # 注释` | ❌ 非法 YAML |
> | `models:  # 注释`（无值） | ✓ 正常 |
> | `models:`（裸键） | ✓ 正常 |
>
> 修复：插入前把带行内值的 `models:` 行**规范化回裸键**（行内值本就要被托管区取代），
> 行内注释保留。六种组合均已加测试锁定。
>
> ⚠️ **后续任务注意**：任何构造 `config.yaml` 文本的代码都必须走 `replace_managed_section`，
> 不要自己拼字符串——这个坑很隐蔽，且在目标机上表现为"服务起不来"。

---

### Task 2：模型配置的读写与校验

**Files:** Modify `models_section.py`（追加）；Test `backend/tests/test_models_section.py`

- [ ] **Step 1：写失败测试**
  - `load_managed_models(config_path)` → 返回托管区内的模型 dict 列表（用 `yaml.safe_load` 解析**仅托管区文本**，不碰全文）
  - `to_public(model)` → 掩码 `api_key`：`sk-abcdefgh1234` → `sk-****1234`；非 `$VAR` 短值同样掩码
  - `build_model_entry(payload)` → 生成符合 `ModelConfig` 的 dict，`use`/`model` 缺失时报错
  - **`validate_candidate_text(candidate_text, *, dir_path)`**：把候选文本写到 `dir_path` 下的临时文件并 `AppConfig.from_file()` 解析；成功返回 `AppConfig` 且**不留下临时文件**；含未定义 `$VAR` 时抛 `ValueError`（复现约束 3 的行为，锁住它）
  - **`commit_config_update(config_path, new_text)`**（五步流程的 4–5 步）：
    - 成功时：返回备份路径；`config_path` 内容 == `new_text`
    - **备份文件存在且内容 == 替换前的原文**（核心断言）
    - **备份写失败时（用只读目录/模拟 OSError），原 `config_path` 内容必须一字未变**
    - `os.replace` 失败时原文件同样不变（原子性）
  - **备份保留策略**：连续 commit 超过 N 次后，最旧的备份被清理，只留最近 N 份
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：实现**
- [ ] **Step 4：跑测试确认通过**
- [ ] **Step 5：提交** `feat(config): add model entry load/mask/validate/commit`

**验收**：
- 掩码函数对 `$VOLCENGINE_API_KEY` 这种引用**不掩码**（它本身不是密钥），只掩码实际值
- 任何失败路径下，原 `config.yaml` **都不被修改**——这是本任务最重要的断言

---

### Task 3：`.env` 写入与进程内环境同步

**Files:** Modify `models_section.py`；Test `backend/tests/test_models_section.py`

> 解决约束 4。这一步漏了，界面保存后模型会**看起来成功但对话失败**。

> ⚠️ **顺序约束（Task 2 实测发现）**：五步协议的第 3 步校验会在**新 key 还没写进 `.env` 时**执行，
> 于是 `$NEW_KEY` 解析为 `None` → `ValueError` → **新增模型的保存必然失败**。
> 实测复现：
> ```
> ValueError: Environment variable VOLCENGINE_API_KEY not found for config value $VOLCENGINE_API_KEY
> ```
> **因此 `.env` 必须先于配置校验写入**，顺序是：
> ```
> 1. upsert_env_var(.env)   ← 先落 key，并同步 os.environ
> 2. 副本替换
> 3. 校验副本                ← 此时 $NEW_KEY 已可解析
> 4. 备份
> 5. 原子替换
> ```
> **但这引入一个新风险**：若第 3–5 步失败，`.env` 里已经多了一个 key，而 `config.yaml` 未改。
> 这个残留是**无害的**（多一个未引用的环境变量不影响任何行为），
> 但要作为已知行为记录，且**不要**为了"干净"而去回滚 `.env`——回滚 `.env` 可能覆盖掉
> 用户手工填入的其他值，风险远大于残留一个无用变量。

- [ ] **Step 1：写失败测试**
  - `upsert_env_var(env_path, key, value)` → 已存在则替换该行，不存在则追加；**其他行原样保留**（`config.yaml` 的处理原则同样适用于 `.env`）
  - 写入后 `os.environ[key] == value`（进程内立即可见）
  - 值含 `#` 或空白时正确加引号（与 `DeerFlow.Common.psm1` 的 `Write-DotEnv` 行为对齐）
  - 幂等：同一 key 连写两次不产生重复行
  - **`$NEW_KEY` 端到端**：先 `upsert_env_var` 落 key，再 `validate_candidate_text` 引用该 key 的配置 → **校验通过**（这是上面顺序约束的回归测试，锁死它）
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：实现**——同时 `open(env_path,'a')` 落盘与 `os.environ[...]=` 同步
- [ ] **Step 4：跑测试确认通过**
- [ ] **Step 5：提交** `feat(config): sync new model keys to .env and process env`

---

### Task 4：Gateway 管理端点

**Files:** Modify `backend/app/gateway/routers/models.py`；Test `backend/tests/test_models_config_api.py`（**新文件，不要追加到 test_models_section.py**）

**派发要点**：不要给子代理全量读 `models_section.py`（724 行）。只给下面这份接口签名，
需要细节时它自己按需 Read 对应函数。

```python
# deerflow.config.models_section — 已可用的接口
MANAGED_BEGIN / MANAGED_END: str

replace_managed_section(text: str, models: list[dict]) -> str
load_managed_models(config_path: Path) -> list[dict]
to_public(model: dict) -> dict                    # api_key 已掩码；$VAR 引用不动
build_model_entry(payload: dict) -> dict          # 缺 use/model 抛 ValueError
validate_candidate_text(candidate_text: str, *, dir_path: Path) -> AppConfig
commit_config_update(config_path: Path, new_text: str, *, keep: int = 10) -> Path
prune_backups(config_path: Path, keep: int = 10) -> list[Path]
upsert_env_var(env_path: Path, key: str, value: str) -> None   # 同时同步 os.environ
```

**固定写入顺序（缺一不可，Task 2/3 实测得出）**：

```python
upsert_env_var(env_path, KEY, value)                       # 1. 先落 .env + os.environ
candidate = replace_managed_section(text, models)          # 2. 副本替换
validate_candidate_text(candidate, dir_path=cfg.parent)    # 3. 校验（失败则原件未碰）
commit_config_update(cfg, candidate)                       # 4+5. 备份 → 原子替换
```

> 顺序 1 必须在 3 之前：否则 `$NEW_KEY` 解析为 None → `ValueError` → 新增模型永远存不上。

- [ ] **Step 1：写失败测试**
  - `GET /api/models/config`：管理员 → 200，含全部字段但 `api_key` 已掩码；普通用户 → 403
  - `PUT /api/models/config`：整体替换托管区；**写后 `config.yaml` 的托管区外内容逐字节不变**
  - `POST /api/models` / `PUT /api/models/{name}` / `DELETE /api/models/{name}`：增删改单条
  - **坏配置回滚**：提交一个 `use` 指向不存在模块的配置 → 4xx 且**原 `config.yaml` 内容未被修改，且不产生备份文件**（校验失败时原件根本没被碰过，不该留下备份垃圾）
  - **成功路径产生备份**：写入成功 → 4xx/2xx 之外还要断言 `config.yaml.bak.<时间戳>` 存在且内容 == 写入前的原文
  - 重名 → 409
  - 每写一次，调用后 `get_app_config()` 能读到新模型（证明热重载生效）
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：实现**——`require_admin_user` 守卫；
  写端点统一走 Task 2 的三个助手，顺序固定：
  ```
  text     = config_path.read_text()
  candidate= replace_managed_section(text, models)
  validate_candidate_text(candidate, dir_path=config_path.parent)   # 失败则抛出，原件未碰
  commit_config_update(config_path, candidate)                       # 先备份，后原子替换
  ```
- [ ] **Step 4：跑测试确认通过**
- [ ] **Step 5：提交** `feat(gateway): add admin model config endpoints`

**验收**：
- 所有写端点均要求管理员
- 校验失败 → 原文件不变**且无备份产生**
- 校验通过 → 备份存在且等于原文，新文件等于候选文本

---

### Task 5：连通性测试端点

**Files:** Modify `backend/app/gateway/routers/models.py`；Test `backend/tests/test_models_config_api.py`

> 没有这个，用户只能"保存了但不知道对不对"。

- [ ] **Step 1：写失败测试**
  - `POST /api/models/test`：给定模型配置 → 发一次最小请求，返回 `{ok, latency_ms, error?}`
  - 无效 key → `ok=false` 且 `error` 可读，**HTTP 仍是 200**（这是业务结果不是接口错误）
  - 网络不可达 → 超时受控（不超过 N 秒），不挂起
  - **测试不写入任何文件**
- [ ] **Step 2：跑测试确认失败**
- [ ] **Step 3：实现**（用 `create_chat_model` + 一句话 prompt，`asyncio.wait_for` 限时）
- [ ] **Step 4：跑测试确认通过**
- [ ] **Step 5：提交** `feat(gateway): add model connectivity test endpoint`

---

### Task 6：前端 API 客户端与 hook

**Files:** Modify `frontend/src/core/models/{api.ts,hooks.ts,types.ts}`

- [ ] **Step 1：扩展 types**——`ManagedModel`（含 `api_key_masked`）、`ModelTestResult`
- [ ] **Step 2：扩展 api.ts**——对应 Task 4/5 的端点；沿用现有 `fetch` 包装与 CSRF header
- [ ] **Step 3：扩展 hooks.ts**——`useManagedModels` / `useSaveModel` / `useDeleteModel` / `useTestModel`；写操作成功后 `invalidateQueries` 刷新模型列表
- [ ] **Step 4：`pnpm check` 通过**
- [ ] **Step 5：提交** `feat(frontend): add model admin API client`

---

### Task 7：设置界面「模型」分区

**Files:** Create `frontend/src/components/workspace/settings/model-settings-page.tsx`；Modify `settings-dialog.tsx`、i18n 三文件

- [ ] **Step 1：实现页面**——列表展示（名称/服务商/地址/状态）、新增、编辑、删除、单条「测试连接」
- [ ] **Step 2：管理员判定**——沿用 `tool-settings-page.tsx` 的模式（403 → 显示"需要管理员"），**不要**自己发请求探测角色
- [ ] **Step 3：注册分区**——`settings-dialog.tsx` 的 `sections` 数组加 `{ id: "models", ... }`，并在渲染分支加 `<ModelSettingsPage />`；**保持现有排序**，建议置于「工具」之前
- [ ] **Step 4：i18n**——`zh-CN.ts` / `en-US.ts` / `types.ts` **三处同步**加 `settings.sections.models` 及各文案（`types.ts` 是类型源，漏改会类型报错）
- [ ] **Step 5：`pnpm check` 通过**
- [ ] **Step 6：提交** `feat(frontend): add model settings page`

**验收**：管理员可见可改；非管理员看到「需要管理员权限」而不是报错。

---

### Task 8：端到端验证与文档

**Files:** Modify `docs/windows-deployment/operations.md`、`docs/windows-deployment/handover.md`

- [ ] **Step 1：同步到目标机**——`./scripts/windows-remote/sync-to-windows.sh`
- [ ] **Step 2：目标机实测**（在 `192.168.2.10` 上，用真实配置）：
  - 界面新增一个模型 → 保存成功
  - **`config.yaml` 的 `diff` 只显示托管区变化**，1471 行注释一行未少
  - **备份已生成**：`config.yaml.bak.<时间戳>` 存在，且与写入前的原文 `diff` 为空
  - 不重启服务，在对话中选用该模型 → 生效
  - 故意填错 API Key → 保存被拒**或**测试连接报错，且**服务仍正常**（`/health` 200）
  - **回滚演练**：用备份文件覆盖回 `config.yaml` → 确认配置回到修改前状态且服务正常
- [ ] **Step 3：更新文档**——`windows-deployment/operations.md` 增补两节：
  1. "通过界面配置大模型"
  2. "配置备份与回滚"——说明备份文件命名、保留策略、回滚命令：
     ```powershell
     # 列出备份
     Get-ChildItem D:\deer-flow\src\config.yaml.bak.* | Sort-Object LastWriteTime -Descending
     # 回滚到指定备份
     Copy-Item D:\deer-flow\src\config.yaml.bak.20260912-143000 D:\deer-flow\src\config.yaml -Force
     ```
  `handover` 增补托管区标记符的说明（**警告不要手工编辑托管区**）
- [ ] **Step 4：提交** `docs(windows): document model configuration UI`

---

## 四、风险与对策

| 风险 | 后果 | 对策 |
|---|---|---|
| 坏配置落盘 | **整个服务所有请求 500** | 五步流程的 1–3 步：改副本、校验副本，失败原件未碰（Task 2/4 显式验收） |
| 校验漏掉运行时问题，替换后才发现 | 原始配置永久丢失 | 五步流程第 4 步：替换前先备份，带时间戳保留历史 |
| 备份写失败仍继续替换 | 以为有备份，实际没有 | 备份失败即中止，不执行 `os.replace`（Task 2 断言） |
| `.env` 写了但进程读不到 | 保存"成功"但对话失败，最难排查 | Task 3 强制 `os.environ` 同步 + 单测锁定 |
| 误删模板注释 | 用户失去参考示例 | Task 1 的核心断言：托管区外逐字节不变 |
| 把密钥回传浏览器 | 凭据泄露给任何登录用户 | 掩码函数 + Task 4 测试断言响应中不含明文 |
| 手工条目被界面无视 | 用户困惑"我加的模型哪去了" | 文档写明：托管区外的手工条目界面不管；不做隐藏 |
| Windows 行尾 CRLF | 混入 `\n` 破坏文件一致性 | Task 1 用 `splitlines(keepends=True)` 保留原行尾 |

---

---

## 四之二、追加需求：改为「服务商 / Base URL / Key」三字段（Task 9）

**用户原话**：「优化大模型配置，改成标准的类型、base url、key 方式，用户无法填写：提供方类路径信息」

### 问题

当前表单让用户填 `use`（形如 `deerflow.models.patched_deepseek:PatchedChatDeepSeek`），
这是**实现细节泄漏**——普通运维既看不懂也不该关心。用户需要的是选「用哪家的模型」。

### 设计

**表单变为：服务商下拉 + 模型名 + Base URL + API Key（+ 显示名、可选开关）**，`use` 由服务商决定。

新增 `GET /api/models/providers`（管理员）返回服务商列表，**动态构建，不硬编码**：

```
对每个候选 (key, label, use, 默认 base_url):
    try: resolve_class(use)  → 收录，available=true
    except Exception:        → 收录，available=false + reason
```

**为什么必须动态**：实测本机 `langchain_ollama:ChatOllama` **解析失败**
（`ImportError: Could not import module langchain_ollama. Missing dependency`）。
若硬编码进下拉框，用户选了它 → 保存时被 validate（use 路径校验）拒绝 → 困惑。
动态列表保证**列出的都是能用的**；不可用的仍列出但置灰 + 显示原因（便于运维知道要装什么）。

候选清单来自 `config.example.yaml` 的 models 段示例（已实测提取），核心几项：

| key | label | use | 默认 base_url |
|---|---|---|---|
| `openai` | OpenAI | `langchain_openai:ChatOpenAI` | （官方默认，留空） |
| `doubao` | 豆包 (火山方舟) | `deerflow.models.patched_deepseek:PatchedChatDeepSeek` | `https://ark.cn-beijing.volces.com/api/v3` |
| `deepseek` | DeepSeek | `deerflow.models.patched_deepseek:PatchedChatDeepSeek` | `https://api.deepseek.com/v1` |
| `kimi` | Kimi (Moonshot) | `deerflow.models.patched_deepseek:PatchedChatDeepSeek` | `https://api.moonshot.cn/v1` |
| `minimax` | MiniMax | `deerflow.models.patched_minimax:PatchedChatMiniMax` | — |
| `anthropic` | Anthropic Claude | `langchain_anthropic:ChatAnthropic` | — |
| `google` | Google Gemini | `langchain_google_genai:ChatGoogleGenerativeAI` | — |
| `openai-compatible` | 其他 OpenAI 兼容 | `langchain_openai:ChatOpenAI` | （用户必填） |

> 具体 base_url 以 `config.example.yaml` 中该服务商示例的 `api_base` 为准；
> 示例里没写的（如 anthropic/google，SDK 自带默认）留空，不臆造。

**编辑已有模型**：从 `use` 反查服务商 key；查不到则回落到 `openai-compatible`
并**保留原 `use` 不变**（不因界面不认识就把用户的高级配置改掉）。

**向后兼容**：后端 `use` 字段照旧接受任意合法类路径——界面只是不再暴露它。
手工编辑 `config.yaml` 的高级用法完全不受影响。

### 验收

- 表单上**看不到** `use` / 类路径
- 选服务商后 `use` 与 base_url 自动填充
- 选一个**解析失败**的服务商时，保存被明确拒绝并说明原因（若该服务商允许列出但不可用）
- 编辑一个 `use` 不在预设表里的模型 → 保存后 `use` **原样不变**
- 既有 `use` 路径校验、五步写入协议、注释保全全部不受影响

---

## 五、明确不做（YAGNI）

- **不动 `models:` 段以外的任何配置段**——工具、技能、沙箱等一律不碰
- **不做服务商预设模板**（原提问里的可选项）——先把增删改查做扎实，预设可作为后续增强
- **不改 `ModelResponse`**——现有 GET 接口的窄契约是有意的，新接口单独开
- **不引 ruamel.yaml**——外科编辑已绕开注释丢失问题，无需新增依赖
