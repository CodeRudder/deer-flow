# MiniMax H3（自建 sglang）参数规范

> 事实源：`scripts/providers/minimax_h3_sglang.py` 的适配器校验。本文件只写连接方式、能力边界与运维注意，不重复能力数值（数值看 `--describe-provider`）。

这是**自建部署**的 MiniMax H3，与云端 `minimax_h3`（`api.minimaxi.com` 的 V2 协议）是**两个独立 provider**，
协议、鉴权、产物获取方式都不同——**不要照搬云端的接入代码**。

## 凭据与端点

- **无鉴权**。不带任何 Token/API-Key 即可调用；适配器的 `auth_headers()` 恒为空。
- 端点由 `SGLANG_H3_API_BASE_URL` 覆盖，默认 `http://100.108.144.120:30010`。
  该变量**不是密钥**，而是地址开关：设置它 = 显式启用本 provider（前端据此显示"已配置"）。
  部署机内部也可用 `http://127.0.0.1:30010`（省一跳）。
- 模型只读环境变量 `SGLANG_H3_MODEL`（默认 `MiniMax-H3-SGLang`）。
- 采样调参（非 CLI 参数，仅环境变量）：
  - `SGLANG_H3_STEPS` —— 推理步数，**服务要求 ≥ 2**（低于 2 会被适配器就地夹到 2），默认 8（官方配方 50，8 是甜点）
  - `SGLANG_H3_SEED` —— 设了才发 `seed`；不设则每次结果不同

## 能力边界

| 能做 | 不能做 |
|---|---|
| `t2va` 文生音视频 | **`ref2va`** 参考图/参考视频/参考音频 —— 本机未加载该 partition，适配器**本地拒绝** |
| `fl2va` 首帧 / 尾帧 / 首尾帧图生视频 | **2K 升格** —— 服务无 `regeneration` 端点，`--upscale-video` 被拒 |
| 一次产出 H.264 画面 + AAC 32 kHz 立体声 | **负面提示词** —— 发布的 checkpoint 是 CFG 蒸馏的，`negative_prompt` 无效 |
| 时长 4–15 秒 | 时长越界、步数 < 2 等都会被拒 |

`--image-role reference` 会被适配器拦下并报"Ref2VA is not loaded on this deployment"。
`--reference-videos` / `--reference-audios` / `--upscale-video` 在 `generate.py` 派发前就被拒（不在 `supported_params` 内）。

**分辨率**：不接受 `768P`/`2K` 这类标签语义之外的云端写法，只认 `384P` / `768P`
（映射为 `target.short_edge`）；分辨率由**短边 + 画幅**共同决定（`384P` + `16:9` → `672x384`）。
**画幅**默认：文生视频 `16:9`，图生视频 `auto`（跟随关键帧）。

## 运维注意（会影响使用策略）

1. **单卡串行** —— 作业依次执行，排队时间 = 前面所有作业的剩余时间。
   不要在网关层并发打入；并发请求会让后到者长时间排队。
2. **600 秒硬天花板** —— 沙箱执行每条 bash 命令有 `timeout=600`（`local_sandbox.py`），
   即**整个 `generate.py` 调用必须在 600 秒内结束**。单条 8 步/768P 作业约 180–270 秒，
   本身安全；但**排队叠加后可能超时**。超时后 sidecar 停在 `pending`（不是 `timeout`），
   用只读的 `--query <task_id>` 追回结果，**不要重新提交**（会白占一次 GPU 且可能重复计费）。
3. **产物不持久化** —— MP4 只在容器内 `/sgl-workspace/sglang/outputs/`，**容器删了就没了**。
   适配器在 `completed` 后立即下载到 `--output-file`，这是唯一可靠的持久化时机。
4. **作业列表会累积** —— 服务端 `GET /v1/videos` 返回全部历史且无分页。
   `--cancel` 只对 `queued` 作业执行 DELETE（因为这个 DELETE 会连产物一起删，已完成作业绝不能删）。
5. **首次加载需 7–9 分钟预热** —— `/health` 返回 ok 只代表进程在，不代表模型已就绪。
6. **无鉴权 + CORS 全开** —— 任何能访问该地址的人都能提交任务并读取**全部作业与产物**。
   目前仅在 tailnet 内可达；**对外暴露前必须自行加鉴权网关**。参见部署侧文档
   `projects/llm/minimax-h3/docs/integration/cloud-parity-gap.md`（P0-3）。
7. **接口文档不可信** —— 该服务的 `/openapi.json` 与 `/docs` 声明的是 sglang 框架默认 schema
   （multipart + `task_type`），**H3 并未采用**，照文档发会 400。契约以
   `projects/llm/minimax-h3/docs/integration/api-guide.md` 为准。

## 图片预检说明

本适配器声明的 `image_spec`（边长/宽高比/格式）**沿用了云端 H3 的数值，未在该部署上重新标定**。
若出现"本地预检拒绝了服务端本可接受的图片"，可放宽或移除该 spec（只影响 `check_materials.py` 预检，
不影响提交）。

## prompt 格式 / 示例

- 同目录 `prompt-format.md`（`[Shot N]` 分镜 + 声音描述的语法）
- 示例：同目录 `examples.md`