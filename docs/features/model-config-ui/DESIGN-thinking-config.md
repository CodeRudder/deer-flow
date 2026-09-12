# 思考（Thinking）配置 — 设计方案

> 状态：设计已定，待实施。

## 问题

模型配置界面只暴露 `name / model / provider / api_base / display_name / api_key` 六个字段，
没有任何 thinking 相关配置，于是 `supports_thinking` 恒为默认的 `false`。

后果不是"少个功能"，是**静默失效**——`config.example.yaml:158` 原话：

> `supports_thinking: true` is required — without it, DeerFlow silently falls back
> to non-thinking mode even when the UI thinking toggle is on.

用户在对话界面打开了思考开关，看起来一切正常，实际没有思考，无任何报错。

## 方案

### 1. 预设表带思考模板

`_ProviderPreset` 增加思考模板字段（与已有的 `api_base_field` 同构），
随 `GET /api/models/providers` 下发。因为**各 provider 的参数形态不同**：

| provider | `when_thinking_enabled` |
|---|---|
| Anthropic | `{thinking: {type: enabled, budget_tokens: N}}` |
| OpenAI 兼容 / DeepSeek / 豆包 / Kimi / MiniMax | `{extra_body: {thinking: {type: enabled}}}` |
| Google | `{thinking_budget: N}` |
| vLLM / Ollama | `{extra_body: {chat_template_kwargs: {enable_thinking: true}}}` |

（取值参照 `config.example.yaml` 既有示例，不臆造。）

### 2. 表单新增「思考能力」区块

```
[✓] 该模型支持思考            [ 检测 ]

思考预算 budget_tokens  [ 4096 ]
最大输出 max_tokens     [ 8192 ]

检测结果：✓ 会思考 / ✗ 关闭不生效 / — budget 未生效
```

- 勾选 → 按该 provider 的模板写入 `supports_thinking` + `when_thinking_enabled` + `when_thinking_disabled`
- 不勾 → 三个字段都不写
- 「检测」手动触发，**不写任何文件**
- 未检测就保存 → 允许，界面标记「未检测」

### 3. 探测端点 `POST /api/models/probe-thinking`（管理员）

用候选配置发一条极短提示，观察响应，返回**三项独立结论**而非单一布尔值：

```
✓ 响应中包含思考内容块      → 端点会思考
✗ 关闭思考后仍返回思考块     → 该端点不支持关闭，界面开关是摆设
— budget 未生效             → 端点忽略 budget_tokens
```

判据是「响应里是否真的出现思考内容」（Anthropic 路径为 `content` 中的
`thinking` 块；OpenAI 兼容路径为 `additional_kwargs.reasoning_content` 等）。

## 关键约束（实测，决定方案形状）

**1. 参数形态按 provider 分叉** —— 与上一轮 `api_base` 问题同源。实测
`model_fields`：Anthropic 用 `thinking`、OpenAI 用 `reasoning_effort`、
Google 用 `thinking_budget`。不能用统一形状填，必须预设表驱动。

**2. 兼容端点对 thinking 参数可能完全不设防** —— 直连 GLM 的 Anthropic 兼容端点
（绕过 DeerFlow）实测：

| 请求 | 结果 |
|---|---|
| 完全不传 `thinking` | 仍返回 thinking 块 |
| `type: disabled` | 仍返回 thinking 块 |
| `budget_tokens: 100`（低于其自报最小值 1024） | HTTP 200 |
| `budget_tokens: 99999`（> max_tokens） | HTTP 200 |

所以**不能靠"有没有报错"判断**，也不能假设配置一定生效。这正是探测要输出
三项独立结论、而不是一个布尔值的原因。

**3. `budget < max_tokens` 只在官方 API 上强制** —— `ChatAnthropic` 内部
`max_tokens` 默认恰好 **4096**，与预算相等，官方 API 上会被拒。故 `max_tokens`
默认取 **8192**。

## 已定参数

| 项 | 默认值 | 说明 |
|---|---|---|
| `budget_tokens` | **4096** | 用户要求：思考输出默认不超过 4k |
| `max_tokens` | **8192** | 被 `budget < max_tokens` 倒逼，非随意取值 |
| 探测触发 | 手动按钮 | 不产生意外 LLM 调用 |
| 未检测就保存 | 允许，标记「未检测」 | 不阻塞流程，但风险可见 |

## 改动范围

| 层 | 改动 |
|---|---|
| 后端 | 预设表加思考模板；新增 `POST /api/models/probe-thinking` |
| 前端 | 类型/API/hook；表单加「思考能力」区块 |
| i18n | 三文件同步 |
| 测试 | 后端：探测判据三种载体、超时、不写文件；前端：勾选/回填/未检测态 |

**不动**：五步写入协议、外科编辑器、注释保全、CRLF 处理 —— thinking 字段只是
`models:` 段里多几个键，走同一条写入路径。
