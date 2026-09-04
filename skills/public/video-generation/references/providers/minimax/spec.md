# MiniMax 参数规范

> 事实源：`scripts/providers/minimax_h3.py` / `minimax_v1.py` 的适配器校验。本文件只写凭据与叙事，不重复能力数值。

## 凭据与端点

- `minimax_h3`（V2，推荐）：专用 key `MINIMAX_VIDEO_API_KEY` 优先，回退共享 `MINIMAX_API_KEY`（音乐/播客 skill 同用）；host 只读环境变量 `MINIMAX_API_HOST`（默认国内站 https://api.minimaxi.com，国际站 https://api.minimax.io）
- `minimax_v1`（legacy Hailuo V1，仅兼容保留）：凭据解析与 h3 相同；模型只读环境变量 `MINIMAX_VIDEO_MODEL`（默认 `MiniMax-Hailuo-2.3`）
- 音频输出：H3 原生立体声，prompt 可含音频描述（音频描述为 H3 独有；v1 无音频）

## minimax_v1（legacy，compatibility only）

- 仅首帧（单图），无音频，`--aspect-ratio` 等参数被忽略（模型侧默认，本地不校验）
- 旧 provider 名 `minimax` 是它的别名（`VIDEO_GENERATION_PROVIDER=minimax` 保持旧行为）
- 新功能一律用 `minimax_h3`

## prompt 格式 / 示例

- T2V / 帧模式：同目录 `prompt-format-base.md`（结构化三字段 / [Shot N] 时间轴）
- reference 模式：同目录 `prompt-format-ref.md`（Ref2VA 六段式）
- 示例：同目录 `examples.md`
