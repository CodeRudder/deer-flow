# 自建（sglang）MiniMax H3 视频接入 — 集成设计记录

- 日期：2026-10-06
- 状态：已实现并端到端验证（部署环境见下）
- 相关：`skills/public/video-generation/`、`backend/packages/harness/deerflow/models/video_generation/registry.py`

## 1. 目标与背景

外部存在一套**自建**的 MiniMax H3 音视频生成服务（sglang 单卡部署，`http://100.108.144.120:30010`），
需要把它接入 DeerFlow 的 `video-generation` skill，并**与云端 MiniMax H3 并存**。

第一直觉是"仓库已有 `minimax_h3` 配置，改 `MINIMAX_API_HOST` 指过去即可"。**这条路走不通**，
实测证据：

| 探测 | 结果 |
|---|---|
| `POST /v2/video_generation`（云端建任务） | **404** |
| `POST /v2/query/video_generation/{id}`（云端轮询） | **404** |
| `POST /v1/video_generation`（legacy V1） | **404** |
| `POST /v1/files/retrieve`（legacy V1） | **404** |
| `POST /v1/videos`（自建实际端点） | 200 |
| `/openapi.json` 的 31 条路径中含 `/v2` 的条数 | **0** |

现有 `minimax_h3` 适配器发送的是 MiniMax **私有 V2 协议**（`content[]`、`task.content.url`、Bearer 鉴权），
自建服务是 **sglang 的 `/v1/videos`**（JSON + `task` 字段、`conditions[]`、二进制 content 端点、无鉴权）。
**协议不同 → 必须新写适配器**。这是本设计的出发点。

## 2. 关键决策

### 2.1 注册为独立 provider，而不是改造现有 `minimax_h3`

云端与自建是**两个独立 provider**。理由是它们在同一维度上全部不同，混在一条配置里无法表达：
协议、鉴权、产物获取、能力集合、并发模型。

| | 云端 `minimax_h3` | 自建 `minimax_h3_sglang` |
|---|---|---|
| 端点 | `POST /v2/video_generation` | `POST /v1/videos` |
| 轮询 | `GET /v2/query/video_generation/{id}` | `GET /v1/videos/{id}` |
| 取产物 | 响应内 `task.content.url` | `GET /v1/videos/{id}/content`（二进制） |
| 鉴权 | `Authorization: Bearer` | **无** |
| 任务表达 | `content[]` + 角色名 | `task` 字段（`t2va`/`fl2va`） + `frame_index` |
| 成功状态词 | `succeeded` | **`completed`** |
| 参考生成 / 2K 升格 | 支持 | **不支持** |
| 并发 | 云端弹性 | **单卡串行** |

### 2.2 模型名必须与云端区分

模型名是 provider 的**路由键**：`providers/__init__.py` 用

```python
MODEL_PROVIDERS = {model: name for name, cls in PROVIDERS.items() for model in (cls.known_models or (cls.default_model,))}
```

构建映射。**重名会被后注册者静默覆盖**，且 `tests/skills/test_video_generation.py::test_model_providers_covers_every_known_model`
会因此失败。故自建模型命名为 **`MiniMax-H3-SGLang`**（云端是 `MiniMax-H3` / `MiniMax-H3-Max`）。

### 2.3 端点用环境变量，且该变量兼作"已配置"信号

`video_generation` 配置段**没有 `base_url` 字段**（schema 里就没有），主机只能由适配器读环境变量。
定义 **`SGLANG_H3_API_BASE_URL`**，默认 `http://100.108.144.120:30010`。

它同时是**前端的"已配置"信号**：后端 `_api_key_configured` 纯按环境变量判断，
而本服务没有密钥可配。所以：

- 设了 `SGLANG_H3_API_BASE_URL` → `configured=true`（显式启用）
- 不设 → 适配器仍可用内置默认地址，但前端显示未配置

代价：`--describe-provider` 会把它显示在 `credential env:` 一行，语义略有拉伸。
`references/providers/minimax_h3_sglang/spec.md` 已明确说明它**不是密钥**。

### 2.4 无鉴权服务的适配器写法（本次唯一的"反直觉"点）

`base.generate()` 开头有一道凭据门禁：

```python
if not self.api_key():
    raise Exception(f"provider={self.name} credential is not set...")
```

服务无鉴权 → 朴素实现会在这里直接抛错。正确做法是**双管齐下**：

- `api_key()` 返回真值哨兵 `"no-auth"`（放行门禁，且不泄露任何凭据）
- `auth_headers()` **覆写为恒返回 `{}`**（不是"没有 key 时才空"——万一有人误设 token，
  往这个服务发 `Authorization` 只会是噪声）

### 2.5 能力边界在本地前置拦截，不交给上游异步失败

`ref2va` 在本部署未加载。若放行，请求会被接受、排队、占用 GPU 后才失败——
**白占一次单卡排队**。故 `image_role="reference"` 在 `create_task` 里直接抛 `ValueError`。
同理 `upscale_video` 与 `reference_videos/audios` 不在 `supported_params` 内，由 `generate.py` 在派发前拒绝。

### 2.6 轮询窗口必须 ≥ 沙箱命令超时

`local_sandbox.py` 对每条 bash 命令用 `subprocess.run(..., timeout=600)` ——
**整个 `generate.py` 调用限时 600 秒**。适配器取 `poll_interval=5` × `poll_max_attempts=240` = 1200 秒，
保证**不早于沙箱放弃**（`test_h3_*_poll_window_not_shorter_than_sandbox_kill` 系列测试钉住此约束）。

## 3. 参数映射

DeerFlow 传入的 `params` 只可能是 `resolution` / `duration` / `ratio` / `image_role` /
`upscale_video` / `reference_videos` / `reference_audios`（后三者本 provider 不接受）。

| DeerFlow | 自建服务 | 规则 |
|---|---|---|
| `resolution` | `target.short_edge`（整数） | `{"384P": 384, "768P": 768}`，默认 768；其它值本地报错 |
| `ratio` | `target.aspect_ratio` | 默认文生 `16:9`、图生 `auto` |
| `duration` | `target.duration_seconds` | 默认 5，校验 `[4, 15]`（服务硬约束） |
| `--reference-images` 有无 | `task` | 无图 → `t2va`（`conditions` 必须为 `[]`）；有图 → `fl2va` |
| `image_role=first_frame`（默认） | `frame_index: 0` | 单条 condition |
| `image_role=last_frame` | `frame_index: -1` | 单条 |
| `image_role=first_last` | `frame_index: 0` 与 `-1` | 两条；只给一张则退化为首帧并告警 |
| `image_role=reference` | — | **本地拒绝**（ref2va 未加载） |
| （非 CLI 参数） | `num_inference_steps` | 服务要求 **≥2**；默认 8（官方配方 50），env `SGLANG_H3_STEPS` 可覆盖，低于 2 就地夹到 2 |
| （非 CLI 参数） | `seed` | 默认**不发**（保持非确定性，与 SKILL.md 一致）；env `SGLANG_H3_SEED` 可设 |

`conditions` 条目**只允许** `type`/`role`/`uri`/`frame_index`/`start_time_seconds` 五个字段，多给即 400；
`uri` 接受 `data:` URI（适配器用 `base.py` 的 `image_ref()`，本地文件自动转 data URL）。

**不发送 `negative_prompt`**：发布的 checkpoint 是 CFG 蒸馏的，负面提示词无效。

## 4. 改动清单

### 技能侧（`skills/public/video-generation/`）

| 文件 | 改动 |
|---|---|
| `scripts/providers/minimax_h3_sglang.py` | **新增** —— 适配器（`create_task` / `poll_once` / `extract_video_url` / `cancel` + 无鉴权处理） |
| `scripts/providers/__init__.py` | 注册进 `PROVIDERS` / `MANIFESTS` / `IMAGE_ROLES`（`MODEL_PROVIDERS` 自动派生） |
| `references/providers/minimax_h3_sglang/{spec,prompt-format,examples}.md` | **新增** —— 凭据/端点/运维注意、`[Shot N]` + 声音描述语法、四种模式示例 |
| `SKILL.md` | 路由表加一行；provider 约束段补该 provider 与 600 秒天花板提示 |
| `references/runtime.md` | 凭据回退链说明（本 provider **不在**回退链内，见 §6） |

### 后端

| 文件 | 改动 |
|---|---|
| `.../models/video_generation/registry.py` | `_PROVIDERS` 增 `_ProviderDefinition`；**模型描述必须与 manifest 逐字一致**（`test_skill_model_descriptions_match_registry_catalog` 钉住） |
| `backend/tests/test_video_generation_registry.py` | 更新 4 处顺序断言 + `_enabled` 的 env 清理 |
| `backend/tests/test_models_config_api.py` | 注释行数夹具 1471 → **1482**（见 §5.2） |

### 配置

| 文件 | 改动 |
|---|---|
| `config.yaml`（本地/部署，**生效**） | `video_generation.providers` 增 `minimax_h3_sglang` 块 |
| `config.example.yaml` | 同块但**注释**（地址是站点专属，不宜硬编码进模板；沿用 `seedance` 的先例） |
| `.env`（部署机） | `SGLANG_H3_API_BASE_URL=http://100.108.144.120:30010` |

### 文档

`README.md` 视频生成段落补充"云端与自建是独立 provider"。

## 5. 测试与验证

### 5.1 新增测试（TDD：先红后绿）

`tests/skills/test_video_generation.py` 新增 **26** 条（`grep -c '^def test_sglang'`），覆盖：请求体默认值与目标结构、
分辨率/时长/画幅校验、首帧/尾帧/首尾帧的 `frame_index` 映射、多余图片告警丢弃、
`reference` 角色拒绝、未知模型拒绝、派发前的参数拒绝、状态归一化（含 404 终止）、
上游错误信息透传、产物 URL 形态、**不发送 Authorization 头**、哨兵放行凭据门禁、
默认地址回退、steps/seed 环境变量覆盖、完整流程（下载 + sidecar）、轮询窗口 ≥600、
cancel 仅对 queued 生效。

`tests/skills/test_video_spec.py` 与 `test_video_generation_registry.py` 的钉住断言同步更新。

### 5.2 测试结论：本次改动引入的回归为 0

后端全量 `76 failed, 6363 passed`。**逐项归因**（用 `git stash` 做基线比对，不靠推断）：

- `test_models_config_api.py` 的 7 条 `thinking_probe_*` —— 基线同样失败，用例名一字不差 → **既有**
- `test_task_tool_core_logic.py` 整文件 27 条 —— 基线同样 27 条，用例名一字不差 → **既有**
- `test_client_live.py` 的 errors —— 标注需真实运行环境的实时集成测试 → **既有**
- `test_outside_managed_region_is_byte_identical_after_writes` —— **本次唯一引入**：
  该测试硬编码 `config.example.yaml` 的注释行数为 1471，而本设计往模板新增了 **11 行注释**。
  按测试自身语义将该常量更新为 **1482**；更新后失败数回到与基线一致的 7。

> 教训：`config.example.yaml` 的注释行数是一个会被测试读到的量。往模板加注释时必须同步该常量。

### 5.3 端到端验证（部署机实测）

```
[create] provider=minimax_h3_sglang handle=11a52e68-1a89-4781-b8c6-4980c077ab6d
[poll] succeeded after 10 polls          ← 约 45 秒
The video has been generated successfully to /tmp/sgl-verify.mp4
```

- 参数：384P / 4 秒 / 8 步（最小组合，减少对共享单卡的占用）
- 产物：`ISO Media, MP4 Base Media v1 [ISO 14496-12:2003]`，160 KB
- sidecar：`provider` / `task_id` / `status=succeeded` / `params` / `billing` 齐全
- `GET /api/video-generation/providers`：`minimax_h3_sglang` → `configured=true`

## 6. 运维约束（会直接影响使用策略）

| 约束 | 说明 | 应对 |
|---|---|---|
| **600 秒天花板** | 沙箱单条 bash 命令 `timeout=600`，整个 `generate.py` 限时 | 单条作业约 45 秒本可容纳，但**单卡串行 + 排队叠加会超时**。超时后 sidecar 停在 `pending`（非 `timeout`），用只读的 `--query <task_id>` 追回，**绝不重新提交**（白占 GPU 且可能重复计费） |
| **单卡串行** | 排队时间 = 前面所有作业的剩余时间 | 不要在网关层并发打入；本 provider **刻意不进凭据回退链**，无法被"顺手"选中 |
| **产物不持久化** | MP4 只在容器内，容器删即失 | 适配器在 `completed` 后立即下载（唯一可靠时机）；下载失败时 sidecar 保留 `provider_succeeded` 便于重取 |
| **作业列表无分页** | `GET /v1/videos` 返回全部历史 | `--cancel` 只对 `queued` 生效（上游 DELETE 会连产物一起删，已完成作业绝不能删） |
| **无鉴权 + CORS 全开** | 任何能访问该地址者可提交任务并读取全部作业与产物 | 目前仅 tailnet 内可达；**对外暴露前必须加鉴权网关** |

## 7. 计费语义（fail-closed，务必记住）

`backend/app/gateway/admin/quota_service.py::_lookup_video_price` 在模型无费率条目时
**直接抛 HTTP 400 `video_billing_rule_not_found`「未配置模型 X 的视频费率」**，生成在派发前即被拒。

**当前部署无风险**：`quota_scopes` 表 0 行（无启用 `video_enforced` 的额度域），
且 `video_billing_rules = {} if not raw_video_rules and not video_enforced`，故根本不查费率。

⚠ **若日后在管理端启用视频积分管控，必须先给 `MiniMax-H3-SGLang` 配费率**
（按分辨率，如 `768P`/`384P`，CNY/秒），否则每次生成都会被 400 拒。
费率键是**精确模型名**（大小写敏感）。

## 8. 何时可以废弃本适配器

上游服务若能提供一个 **MiniMax V2 兼容层**（暴露 `/v2/video_generation` 与
`/v2/query/video_generation/{id}`，把 `content.url` 指向现有的 content 端点），
则本适配器即可删除，改由现有 `minimax_h3` 适配器 + `MINIMAX_API_HOST` 接入。

已就此产出面向服务侧的改进建议文档（含此建议及其余缺口，按优先级排列）：
`projects/llm/minimax-h3/docs/integration/cloud-parity-gap.md`。
其中 P0 还包含：修正 `/openapi.json`（它声称 multipart + `task_type`，与实现不符，
是每个新接入方必踩的坑）、补鉴权、参数校验前移到同步阶段。

## 9. 复用套路：再接一个同类 provider 要动哪些地方

供后续接入其它自建/第三方视频服务时参考（`seedance` 与本 provider 都是这么加的）：

1. `scripts/providers/<name>.py` —— 实现 `api_key()` / `create_task()` / `poll_once()` /
   `extract_video_url()`，可选 `cancel()`；模块级导出 `PROVIDER` / `PROVIDER_MANIFEST` /
   `IMAGE_ROLES` / `MODEL_SPECS` / `DEFAULT_MODEL`；类属性 `name` / `supported_params` /
   `api_key_envs` / `known_models` / `poll_interval` / `poll_max_attempts`
2. `scripts/providers/__init__.py` —— 三张注册表各加一行
3. `references/providers/<dir>/{spec,prompt-format,examples}.md` —— 目录与文件集被
   `test_provider_reference_folders_are_complete` 钉住
4. `SKILL.md` —— 路由表加行（`test_routing_table_covers_manifest_prompt_files` 会校验）
5. `backend/.../video_generation/registry.py::_PROVIDERS` —— 描述与 manifest **逐字一致**
6. `config.yaml` + `config.example.yaml`
7. 测试：`tests/skills/test_video_generation.py` 的 `set(PROVIDERS)` 断言与 `clean_env` env 清单、
   `tests/skills/test_video_spec.py` 的目录预期、
   `backend/tests/test_video_generation_registry.py` 的顺序断言

**易踩的三个点**：模型名必须全局唯一（§2.2）；无鉴权服务要处理凭据门禁（§2.4）；
轮询窗口必须 ≥600 秒（§2.6）。