# Seedance 参数规范

> 事实源：`scripts/providers/seedance.py` 的适配器校验。本文件只写凭据与叙事，不重复能力数值。

## 凭据与端点

- 专用 key `SEEDANCE_VIDEO_API_KEY` 优先，回退共享 `ARK_API_KEY`（与方舟 chat 模型同族）
- base_url 默认 https://ark.cn-beijing.volces.com/api/v3；`SEEDANCE_API_BASE_URL` 可覆盖（BytePlus 国际站）
- 模型只读环境变量 `SEEDANCE_VIDEO_MODEL`（默认 2.5）
- 参考视频/音频仅接受公网 URL（API 拒绝 base64、无法取本机路径；请求体上限 64 MB）
- 音频输出：默认生成（对白用 `{}`、BGM/SFX 语法见 prompt-format.md）

## 任务锁定（为什么）

- 2.5 帧任务锁 `ratio=adaptive`：输出跟随首帧图比例，显式非 adaptive 会被上游**异步**拒绝（任务先排队再失败），故适配器本地前置拦截
- 仅视频编辑任务另锁 `duration=-1`；2.0 系列无锁定
- 2.0 系列不接受纯音频输入（需至少一个参考图/视频；仅 2.5 支持音频单独）

## prompt 格式 / 示例

- 同目录 `prompt-format.md`（@素材标签 + 时间组织；2.5 与 2.0 的时间表达分支见其 Model family branch 节）
- 示例：同目录 `examples.md`
