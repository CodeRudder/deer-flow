# MiniMax H3（自建 sglang）worked mode examples (T2V / first / last / first+last)

Loaded on demand when the routed provider is `minimax_h3_sglang`. All examples
below assume the input-table and plan-card gates have passed and the prompt file
was written in the `[Shot N]` + soundscape format (Step 2, `prompt-format.md`
in this directory).

> 该 provider **无鉴权**、只支持 `t2va`（文生）与 `fl2va`（首/尾帧图生）。
> 不支持 reference 模式与 2K 升格 —— 传入会被本地拒绝。

## SGL-1 — 文生音视频（T2V）

User: "日出时分的静湖，缓慢推镜，要有水声和轻钢琴。"

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sgl-t2v.txt \
  --provider minimax_h3_sglang --model MiniMax-H3-SGLang \
  --resolution 768P --duration 5 --aspect-ratio 16:9 \
  --output-file /mnt/user-data/outputs/sgl-t2v.mp4
```

Prompt file:

```
[Shot 1] Cinematic, medium wide shot, pushing in slowly. A serene mountain lake
at sunrise, mist drifting over the water.
overall_soundscape: gentle water lapping, distant birdsong
non_diegetic_music: soft piano, slow tempo
```

## SGL-2 — 首帧生视频（first_frame，默认）

User uploaded `frame.png`: "让这张图动起来，镜头缓慢横移。"

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sgl-i2v.txt \
  --reference-images /mnt/user-data/uploads/frame.png \
  --provider minimax_h3_sglang --model MiniMax-H3-SGLang \
  --resolution 768P --duration 5 \
  --output-file /mnt/user-data/outputs/sgl-i2v.mp4
```

`--image-role` 缺省即 `first_frame`；画幅默认 `auto`（跟随关键帧）。
提示词只描述**运动**，不要重复描述图上已有内容。

## SGL-3 — 尾帧生视频（last_frame）

User uploaded `target.png`: "让视频收束到这一帧。"

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sgl-last.txt \
  --reference-images /mnt/user-data/uploads/target.png \
  --image-role last_frame \
  --provider minimax_h3_sglang --model MiniMax-H3-SGLang \
  --resolution 768P --duration 5 \
  --output-file /mnt/user-data/outputs/sgl-last.mp4
```

## SGL-4 — 首尾帧（first_last）

User uploaded `dawn.jpg` and `dusk.jpg`: "从天亮过渡到黄昏。"

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/sgl-fl.txt \
  --reference-images /mnt/user-data/uploads/dawn.jpg /mnt/user-data/uploads/dusk.jpg \
  --image-role first_last \
  --provider minimax_h3_sglang --model MiniMax-H3-SGLang \
  --resolution 768P --duration 8 \
  --output-file /mnt/user-data/outputs/sgl-fl.mp4
```

第一张是首帧、第二张是尾帧。**只给一张**时会退化为首帧模式并打印告警；
给三张以上只取前两张（`first_last`）或第一张（`first_frame`/`last_frame`）并告警。

## 超时追回（重要）

沙箱对每条 bash 命令有 600 秒上限。若生成因**排队**超时被沙箱杀掉，
sidecar 停在 `pending`，**不要重新提交**（会白占 GPU 且可能重复计费），改用只读查询追回：

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --query <task_id> \
  --provider minimax_h3_sglang --model MiniMax-H3-SGLang
```

状态为 `completed` 后再下载到**新路径**（`--output-file` 不允许覆盖已存在文件）。

## 取消排队中的作业

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --cancel <task_id> \
  --provider minimax_h3_sglang --model MiniMax-H3-SGLang
```

只对 `queued` 状态生效；已完成的作业不会被删除（服务端的 DELETE 会连产物一起删）。