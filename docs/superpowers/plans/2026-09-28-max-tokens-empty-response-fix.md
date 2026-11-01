# 修复方案：空响应重试对 max_tokens 截断无效（方案 B）

> **日期:** 2026-09-28
> **关联故障:** 会话 `b6a47375`（茂名旅游方案规划）反复出现
> "LLM returned an empty response after multiple retries" 后对话终止。
> **关联配置问题（已另行处理）:** 模型条目缺 `max_tokens`，思考 `budget_tokens=4096`
> 烧满 fallback 上限 4096，正文零字。

---

## 1. 根因回顾

三层叠加：

| 层 | 事实 | 证据 |
|---|---|---|
| 配置 | `glm-5.3-flash` 条目无 `max_tokens`，`budget_tokens: 4096` | 远端 `config.yaml` |
| 依赖 | `langchain_anthropic` 对未知模型名 fallback `_FALLBACK_MAX_OUTPUT_TOKENS = 4096` | `chat_models.py:96` |
| 协议 | Anthropic 的 thinking 计入 `max_tokens`；预算(4096) ≥ 上限(4096) → 思考烧满即截断，`stop_reason=max_tokens`，正文为空 | 日志 `output=4096` 恰好 5 次 |

**中间件的缺陷**：`llm_error_handling_middleware.py` 对空响应一律走「退避重试 3 次 → 报错」。
但 max_tokens 截断是**确定性失败**——同样的 prompt 必然再次烧满思考，重试纯属浪费
（实测 3 次重试约 4 分钟，全部失败）。且最终报错文案误导运维去查"模型服务状态"。

## 2. 修复目标

空响应**且** `stop_reason` 表明是输出上限截断时：

1. **不重试**（失败是确定性的，重试不改变结果）
2. **不计入熔断器**（这是配置问题，不是服务商可用性问题）
3. **给出可操作的报错文案**：指明"输出上限不足"及修复路径（调大 max_tokens / 调小思考预算）

非截断的空响应（服务商偶发抽风等）**保持现有重试行为不变**。

## 3. 改动点

文件：`backend/packages/harness/deerflow/agents/middlewares/llm_error_handling_middleware.py`

### 3.1 新增检测 helper（放在 `_is_empty_ai_response` 旁）

```python
@staticmethod
def _stopped_at_max_tokens(result: ModelCallResult) -> bool:
    """True when the empty response was caused by hitting the output cap.

    LangChain providers surface the truncation reason in two shapes:
    Anthropic-style `response_metadata["stop_reason"] == "max_tokens"` and
    OpenAI-style `response_metadata["finish_reason"] == "length"`. Check both
    so the fix covers every provider, not just the ChatAnthropic path that
    produced this incident.
    """
```

从 `AIMessage.response_metadata` 里取 `stop_reason` / `finish_reason` 判断；
`response_metadata` 缺失或非 dict 时返回 False（保守：走原重试路径）。

### 3.2 新增报错文案常量（放在 `_EMPTY_RESPONSE_FALLBACK` 旁）

```python
_MAX_TOKENS_FALLBACK = (
    "The model used its entire output budget (thinking + answer) and returned "
    "nothing. This is a configuration limit, not a service outage: raise "
    "max_tokens or lower the thinking budget in Settings -> Models, then retry."
)
```

### 3.3 两个重试循环各插一个短路分支

- 同步循环（现 `:399` `if self._is_empty_ai_response(response):` 之后）
- 异步循环（现 `:474` 同款判断之后）

```python
if self._stopped_at_max_tokens(response):
    logger.warning(
        "Empty AI response caused by max_tokens truncation (stop reason); "
        "not retrying - the same prompt will truncate again. Fix: raise "
        "max_tokens or lower the thinking budget."
    )
    return self._finalize_error_fallback_message(
        self._build_error_fallback_message(
            _MAX_TOKENS_FALLBACK, error_type="MaxTokensTruncation",
            reason="max_tokens", detail="stop_reason indicates output cap",
        ),
        request,
    )
```

要点：**放在重试判断之前**；**不调用 `_record_failure()`**（不推动熔断）。

## 4. 测试（TDD，先写失败测试）

文件：`backend/tests/test_llm_error_handling_middleware.py`（已有，追加）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 空响应 + `response_metadata={"stop_reason": "max_tokens"}`（同步路径） | handler 只被调 1 次；返回文案含 "output budget"；不计熔断 |
| 2 | 空响应 + `{"finish_reason": "length"}`（OpenAI 形态，同步） | 同上 |
| 3 | 空响应 + `stop_reason=max_tokens`（异步路径） | 同用例 1 |
| 4 | 空响应 + 无 stop_reason（回归） | 仍是原 3 次重试 → `_EMPTY_RESPONSE_FALLBACK`，行为不变 |
| 5 | 空响应 + `stop_reason="end_turn"`（回归） | 同用例 4 |

用 monkeypatch 把 `_empty_retry_delay_ms` 归零避免测试睡眠（沿用该文件现有手法）。

## 5. 部署与验证

1. `cd backend && uv run pytest tests/test_llm_error_handling_middleware.py -v`（先红后绿）
2. `make lint`（ruff）
3. 同步到 gdw-pc-mini → 重启 Gateway（**中间件是代码，非热加载**）
4. 端到端验证：用触发过故障的同类长任务对话跑一次；若配置侧 max_tokens 已同时调大，
   则额外用**临时调回 max_tokens=4096 + thinking=4096** 的方式复现"截断"，
   确认新文案出现且**不重试**（日志只 1 次调用），随后恢复配置。

## 6. 明确不做

- 不动 `_is_empty_ai_response` 的判定逻辑（带 tool_calls 的响应不算空——既有语义）
- 不在本方案里调模型配置（方案 A 的 `max_tokens: 16384` 由运维在管理界面改，与本方案独立）
- 不做"自动重试时削减思考预算"之类的智能降级（复杂度高、行为难预测，收益存疑）
