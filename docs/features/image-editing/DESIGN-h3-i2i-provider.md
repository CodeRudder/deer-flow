# image-editing 接入自建 H3 图生图 — 集成设计记录

配套阅读：[`docs/features/video-generation/DESIGN-self-hosted-h3-integration.md`](../video-generation/DESIGN-self-hosted-h3-integration.md)
（自建 H3 服务的整体接入决策，含 600 s 沙箱天花板与产物不持久化的推导）。
本篇只记录 **image-editing 侧新增 `h3_i2i` provider** 这一个决策面。

## 1. 目标与背景

`image-editing` 原本只有一个 provider `openai_image_edit`，它要求 `config.yaml` 里
提供一个非空的 `Authorization`（Azure OpenAI 的部署端点）。

自建 H3 网关的图生图档（`h3-i2i-std` / `h3-i2i-hq`）已经接在 **image-generation** 上，
但那条路径的契约是「参考图仅作 loose inspiration」，而 image-editing 的契约是
「保结构、未编辑区域不变」。用户要求让 image-editing 也能用 `h3-i2i-*`。

## 2. 关键决策

### 2.1 新开一个 provider，而不是给 `openai_image_edit` 加分支

两个 provider 的**凭据模型**根本不同：一个吃 operator 提供的 header，一个自己从
环境变量构造 header。把它们塞进一个函数会让「要不要 Authorization」变成一个
埋在函数体里的 if，而 `edit.py` 的闸门需要在**调用之前**就知道答案。

### 2.2 凭据闸门改成 provider 感知（本次唯一的"反直觉"点）

`edit.py` 在派发前无条件要求 `Authorization`，否则报
`... is missing Authorization in config.yaml`。`h3_i2i` 没有也不需要这个值。

因此闸门改为读被派发**可调用对象自身**的属性：

```python
if not authorization and getattr(PROVIDERS[selected_provider], "REQUIRES_AUTHORIZATION", True):
    raise ValueError(...)
```

对应地，`h3_i2i.py` 在函数定义**之后**挂属性，而不是在模块级定义常量：

```python
edit.REQUIRES_AUTHORIZATION = False
```

理由：`PROVIDERS` 字典里存的是**函数**，模块级常量到不了派发点。默认值是 `True`，
所以忘记声明的第三方 provider 仍然按老行为硬失败——**这是刻意的**，放宽的方向只有
"provider 明确声明自己不需要"，不会出现静默地不带凭据发包。

`test_edit_still_requires_authorization_for_a_provider_that_needs_one` 是这条的回归护栏。

### 2.3 边界必须在 SKILL.md 里写明，而不是靠路由规避

H3 图生图的真实语义是：参考图作为短片**首帧**，模型演化后**取末帧**返回。
即"按提示词演化/重绘"，**不保证未编辑区域像素级不变**，也**没有 mask/inpainting**。

这一点与 image-editing 的立身之本直接冲突，所以：

- 不假装它保结构。SKILL.md 的新 "Providers" 一节给出对照表，并明确要求 agent 在
  `h3_i2i` 生效时**不要向用户宣称未改动的部分是保留的**；真正需要保真的任务，应直说
  "本部署没有满足该契约的 provider"，而不是默默交一张重绘图。
- 结构保真路由（设计图→实物图）仍归 image-editing，不因这个 provider 而改变。

### 2.4 轮询预算复用共享的 `timeout`，取值 540

`edit.py` 把 `provider_config.timeout` 作为 `timeout_seconds` 传给 provider，
provider 用它当轮询预算（`H3_IMAGE_POLL_TIMEOUT_SECONDS` 可覆盖）。

配置里给 `h3_i2i` 的 `timeout: 540` 而非默认 300，因为网关**视频优先让行**——
实测一次 `h3-i2i-std`（档位标称 ~31 s）因让行 1 个视频作业，实际跑了 **4 分 14 秒**。
540 仍在 600 s 沙箱硬顶之下，留 60 s 余量给解法。

### 2.5 超时/让行失败 = 重跑同一条命令

沿用在 image-generation 侧已验证的机制：请求带内容派生的 `Idempotency-Key`，
24 h 内同 key 同 body 返回**原作业**。所以：

- 超时文案明确写"重跑同一条命令会重新附着此作业（不会二次占用 GPU）"，并给出
  `GET /v1/images/jobs/{id}/content?variant=0` 作为直接取回的后路。
- `H3_IMAGE_NO_IDEMPOTENCY=1` 是刻意重掷的逃生口——否则同 prompt 永远拿回同一张图。

**实测**：首跑 `img_e8a3bf621660`（4m14s），重跑同命令 **6.5 s** 拿回同一 job id 与
同一 `seed`。

### 2.6 默认 provider 的取值随部署而定

两处 config 都设为 `default_provider: h3_i2i`，因为**两地都没有可用的 OpenAI 凭据**：

- 本地无 `.env`；服务器 `.env` 里 `OPENAI_IMAGE_AUTHORIZATION=` 长度为 0。
- 服务器有 `H3_IMAGE_AUTH_TOKEN`（48 字符），经 `EnvironmentFile=-/opt/deer-flow/.env`
  进入进程环境。

`config.example.yaml` 作为中性模板仍保持 `default_provider: openai_image_edit`，
只把 `h3_i2i` 作为可选 provider 列出。

## 3. 改动清单

| 文件 | 改动 |
|---|---|
| `skills/public/image-editing/scripts/providers/h3_i2i.py` | 新增（提交/轮询/落盘，`edit.REQUIRES_AUTHORIZATION = False`） |
| `skills/public/image-editing/scripts/providers/__init__.py` | 注册 `h3_i2i` |
| `skills/public/image-editing/scripts/edit.py` | 凭据闸门改为 provider 感知；`authorization` 空值不再传字面量 `"None"` |
| `skills/public/image-editing/SKILL.md` | 新增 "Providers" 一节（对照表、边界、参数、环境变量、重试语义） |
| `config.yaml` / `config.example.yaml` | `image_editing` 增加 `h3_i2i` 段 |
| `tests/skills/test_image_editing.py` | 新增 9 个 h3_i2i 用例 |
| `backend/tests/test_models_config_api.py` | 注释计数断言 1518 → 1528（模板新增注释） |

后端**没有** image_editing registry、没有 API 路由——只有 skill 脚本自己在运行时读
`config.yaml`（`_find_config_path()`）。所以本次改动**不需要重启** Gateway。

## 4. 未做（有意）

- **mask / inpainting**：网关不支持，`--mask` 对所有 provider 继续显式报错。
- **多参考图**：`h3_i2i` 只接受恰好 1 张；0 张或 2 张在本地就报错，不占 GPU。
- **`--size` / `--quality` / `--output-format` / `--api-version`**：接受但忽略并告警，
  输出恒为 1344×768 PNG。
- **不改 image-generation 的路由建议**：`h3-i2i-*` 仍同时存在于 image-generation；
  两条路径的差别只在契约表述，不影响本次改动。

## 5. 验证

- `cd tests/skills && uv run --project ../../backend pytest -q` → 220 passed（含新增 9 例）
- `backend/tests/test_models_config_api.py` → 除 7 个**改动前就已失败**的
  `thinking_probe_*` 外全绿（已用 revert 对比确认既有失败与本次无关）
- 部署机 47.119.152.208 端到端：`--provider h3_i2i --model h3-i2i-std` 出真实
  PNG 1344×768 / 1.6 MB，日志 `extracting frame 21/22` 证实末帧语义
- 幂等重连：同命令二次调用 6.5 s 返回同一 job id 与 seed