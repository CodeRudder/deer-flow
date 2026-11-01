# MiniMax H3（自建 sglang）prompt 格式

H3 是**音视频联合生成**模型：一次请求同时产出画面与立体声。因此 prompt 不只是"画面描述"，
而是要同时交代**镜头**、**环境声**与**配乐**。官方控制台用的就是下面这套模板，
对"分镜 + 声音"式描述响应最好。

## 模板

```
[Shot N] <镜头语言：景别、运镜、速度>。<主体与场景描述>
overall_soundscape: <环境声>
non_diegetic_music: <配乐，含速度>
```

三段都可缺省，但**给了声音描述才能拿到贴合的音频**。

## 什么时候需要 `[Shot N]`

- **单镜头（≤15 秒短视频）**：通常一个 `[Shot 1]` 即可，多写分镜反而会让 15 秒内塞不下。
- **多镜头**：按 `[Shot 1]`、`[Shot 2]`…… 顺序书写，模型按顺序生成时间轴。

## 镜头语言怎么写

把景别与运镜写具体，模型对具体动词响应更好：

| 维度 | 例子 |
|---|---|
| 景别 | `extreme close-up` / `medium wide shot` / `full shot` |
| 运镜 | `pushing in slowly` / `slow pan across` / `handheld, subtle drift` / `static, locked off` |
| 速度 | `slowly` / `gently` / `rapidly` |

## 声音两行的语义

- `overall_soundscape:` —— **画面内**的环境声（水声、鸟鸣、脚步、风声、街道底噪）
- `non_diegetic_music:` —— **画面外**的配乐（观众听到但角色听不到），写明速度与情绪

例：`overall_soundscape: gentle water lapping, distant birdsong` /
`non_diegetic_music: soft piano, slow tempo`

## 硬约束

- **不要写负面提示词** —— 该 checkpoint 是 CFG 蒸馏的，负面提示词无效。
- **不要假设有台词/对白语法** —— 云端 Ref2VA/对白标签体系（`<Picture N>`、`<d>`）属于云端规范，
  本适配器不支持 reference 模式，不适用。
- 提示词**建议写成镜头描述而非关键词堆砌**。

## 图生视频（fl2va）的提示词

仍然是同一模板，但应描述**从关键帧出发的运动**，不要重复描述画面中已存在的静态内容：

```
[Shot 1] Slow pan across the scene, subtle drift. overall_soundscape: low ambience
```

首帧 / 尾帧 / 首尾帧由 `--image-role` + 图片顺序决定（见 `examples.md`），提示词里不必再声明。