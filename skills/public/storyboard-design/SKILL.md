---
name: storyboard-design
description: Use this skill when the user asks 分镜设计 / 分镜 / storyboard / 镜头设计 / 白模布局（blockout）— e.g. gives a 主题 / 剧本概要 / 参考视频片段 and wants 结构化分镜设计文档（含整体场景/角色/道具设定、动作前中后三态、人物占位与视点、主动/被动变化、运动物体轨迹跟踪、运镜方法）. 纯设计层 — outputs JSON+markdown + optional 顶视白模参考图 for the generation layer (drama-production / video-clips) to consume; 不调任何生成 API、不落 MGW 库.
---

# 分镜设计智能体 Skill（storyboard-design）

## Overview

本 skill 是**纯设计层**：输入主题 / 剧本概要 / 参考视频片段，输出**结构化
分镜设计文档**（JSON + markdown，可选顶视白模布局参考图），供生成层消费——
生成执行一律引导至 `drama-production`（MGW drama shots 落库 + 逐镜生成 +
QC 闭环）或 `video-clips`（组图生视频 storyboard shots）。本 skill **不调
任何生成 API、不写 MGW**，只产出可直接映射到落库契约的设计文件。

沙箱执行入口：`scripts/design_storyboard.py`（零第三方依赖；白模渲染可选
依赖 PIL，不可用时优雅降级）：

```bash
python /mnt/skills/public/storyboard-design/scripts/design_storyboard.py \
  --theme "羽毛球对打" --shots 3 \
  --script-file 剧本概要.md \
  --out-dir /mnt/user-data/outputs/storyboard/badminton
```

- 常用参数：`--shots`（1–20，缺省 3）、`--scene`（场景一句话）、
  `--script-file`（剧本/概要文件，全文并入 `script_summary`）、
  `--from-video <参考视频>`（ffmpeg 抽帧辅助观察，`--frame-step` 抽帧间隔
  秒）、`--blockout`（顶视白模布局参考图，见下节）、
  `--validate <json>`（schema 门禁校验）、`--example`（内置羽毛球范本）。
- 产物写入 out-dir：`<主题>.storyboard.json` + `<主题>.storyboard.md`
  （+ `blockouts/shotNN-blockout.png`）。
- **骨架模式**：脚本只生成待填骨架并列出待填清单（checklist）——动作三态、
  运镜、轨迹等设计内容由你（LLM）逐镜填写后重跑 `--validate`；校验通过
  才算设计完成，未过校验的设计文档不得交给生成层。

## 流程

1. **输入收集**：主题/剧本概要必填其一；有参考视频/片段则先抽帧观察
   （`--from-video`，帧图仅供观察提炼，不拷入仓库/产物库）——提炼写法看
   「内置羽毛球示例」：每镜从帧序列读出动作三态、占位、轨迹、运镜时机。
2. **整体设定层**：场景（地点/时间/氛围/光线）、角色卡（外观/服装/姿势
   基线；`media_id`/`subject_id` 留空待资产登记后回填）、道具卡（运动道具
   如球类要标注轨迹特征）。
3. **逐镜设计**（LLM 填骨架，字段语义见下「schema 速查」）：动作三态 →
   占位/视点（主动 vs 被动变化分开）→ 运镜 → 运动物轨迹 → 衔接。
4. **校验**：`--validate` 过 schema 门禁（衔接连续性、三态齐备、运镜四
   要素、轨迹关键帧）。
5. **白模（可选）**：`--blockout` 出顶视布局参考图。
6. **交付**：JSON + markdown（+ 白模图）交用户目检；用户要"生成视频"时，
   **引导至 `drama-production`（短剧逐镜生成）或 `video-clips`（组图生视频
   ）**——本 skill 到设计交付为止。

## Schema 速查（每 shot 一份结构）

### 整体设定层（storyboard 顶层）

| 字段 | 写什么 |
|---|---|
| `title` / `logline` / `style` | 主题 / 一句话梗概 / 全片统一风格段 |
| `scene` | 场景：`location` / `time` / `mood` / `light` |
| `characters[]` | 角色卡：`appearance` / `baseline_pose`（姿势基线，跨镜引用）+ `media_id`（角色资产引用，落库后回填） |
| `props[]` | 道具卡；**运动道具**（球/抛掷物）在 `notes` 标注轨迹特征（阻尼、稳定姿态、弹跳） |

### 每 shot（五组维度 + 落库映射）

| 维度 | 字段 | 落库映射（drama shots / storyboard shots） |
|---|---|---|
| 动作三态 | `action.before/during/after`（+`delta` 变化量） | 三态合成一句"从哪开始→过程→结束"写入 drama `prompt_zh`（主稿）/ video-clips `action` |
| 占位与视点 | `placement.figure/occupancy`；`viewpoint.shot_size/angle/seen` | `seen` 并入 prompt 正文；`shot_size/angle` → drama `size`/`camera` |
| 主动/被动变化 | `viewpoint.active_changes` / `passive_changes` | 分开写成正文短语（"主动发力…"/"被动受击…"），生成层据此判定动作归属 |
| 运镜方法 | `camera_movement.type/path/timing/speed` | `type+path` → drama `camera` / video-clips `camera`；`timing/speed` 并入 prompt 正文 |
| 运动物体轨迹 | `motion_objects[].trajectory[]`（`phase`+`pos` 关键帧）+ `speed_feel` + `enter_exit` | 并入 prompt 正文（轨迹描述）；`speed_feel`（慢镜/正常速）标节奏；跨镜轨迹靠 `transition` 连续 |
| 镜头衔接 | `transition.entry/exit` | 校验强约束：**上一镜 exit = 下一镜 entry**（忽略括注后） |
| 台词/参考 | `dialogue[]`（`speaker` 必填）、`refs[]` | drama `dialogue`（speaker 必填）/ drama ref_tray `<Picture N>`、video-clips `refs`（1-based） |

字段名与 MGW 落库契约**有意不同名**（设计层更细）：映射关系如上表与脚本
`--validate` 的注释；对接 drama-production 时按其 SKILL.md 阶段 2 的
五要素位置落库，不改后端。

### 运镜术语速查（`camera_movement.type` 枚举）

| 类型 | 含义 | 常配时机（`timing`） |
|---|---|---|
| 固定 | 机位不动（lock-off） | 定场/收束镜 |
| 推 | 机位向前靠近主体 | 强调细节：触球/表情瞬间推近 |
| 拉 | 机位后退远离 | 收束展全场：随挥后拉远 |
| 摇 | 机位不动转角（pan/tilt） | 主体横移但需保持机位 |
| 移 | 横向平移（truck/dolly） | 并行跟随运动物 |
| 跟 | 跟随主体/运动物 | 球类飞行段（如"跟镜：随球过网横向平移"） |
| 升降 | 垂直升降（crane/pedestal） | 开场定场/高潮俯瞰 |
| 环绕 | 绕主体 orbit | 英雄时刻/展示空间 |
| 手持 | 手持感（含抖动） | 紧张/纪实段 |
| 自由 | 自由描述（`path` 里写） | 复合运镜 |

`path` 写起止机位与朝向变化；`timing` 写与动作三态的同步关系（如"击球
瞬间急推近，球起飞后改跟移"）；`speed` 用 缓/常/急（可复合"急→常"）。

## 白模（blockout，默认关闭，`--blockout` 开启）

- **是什么**：每个 shot 一张**顶视 2D 布局草图**（PIL 几何图元，非 AI 生图
  、不依赖网关）：场地边界 + 中线（网）、角色圆点+编号+朝向箭头、球轨迹
  折线+时间标注、机位十字+运镜箭头。
- **数据源**：shot 的 `blocking` 字段（顶视坐标系 x/y ∈ 0–100）：
  `characters[] {name,pos,facing_deg,label}`、`camera {pos,target,move_to}`、
  `paths[] {name,points,times}`。`blocking` 与文字字段一一对应：
  `characters[].pos` ↔ `placement.figure`；`paths` ↔
  `motion_objects[].trajectory`；`camera.move_to` ↔ `camera_movement.path`。
- **用途**：给生成层（t2v/i2v 首帧或角色参考）提供位置/轨迹的**确定性
  约束**，与文字描述互补——文字管质感，白模管"谁在哪、朝哪、球走哪、
  镜头怎么走"。只给关键镜配白模时给该 shot 加 `"key_shot": true` 并用
  `--blockout-key-only`。
- PIL 不可用时脚本打印警告并把 `blockout` 置空，**不致命**（文字字段仍然
  完整）。

## 内置羽毛球示例（写法范本）

`--example` 打印完整范本 JSON；`--example --blockout --out-dir <dir>` 同时
产出 3 张白模。要点摘录（源自 ref-review 羽毛球系列片段抽帧观察）：

- **镜 1 发球准备**：动作三态"持拍侧身（前）→ 抛球+引拍（中）→ 悬停在
  引拍顶点（后）"，镜止于"即将击球"瞬间；运镜=推（抛球瞬间开始推）；球
  轨迹起点=指尖、止于上升段不落镜内。
- **镜 2 击球过网**：全片主动变化峰值（蹬地+转体+挥拍+随挥）；运镜=跟
  （击球瞬间急推拍面触球特写→球起飞改"随球过网横向平移"）；球速 0→峰值
  0.1s，可插 2–3 帧慢镜。
- **镜 3 接球收拍**：运镜=拉（回球过网瞬间开始拉成全景收束）；回球减速
  下坠段慢镜强调（羽毛球高阻尼特征）；结束姿态=镜 1 起始基线（闭环可循环）。

写法规则：动作三态每段给**可拍的具体姿态**（不是情绪词）；主动/被动变化
分句写；球轨迹必须给起点→高点→落点三关键帧；跨镜衔接用原句复写。

## 边界与不做的事

- **不生成视频/图片**：生成执行引导至 `drama-production`（短剧）/
  `video-clips`（组图生视频）；白模是几何草图不是 AI 生图。
- **不写 MGW**：不调任何 Media Gateway 端点、不落库；`media_id`/`subject_id`
  只留占位待生成层回填。
- **不拷参考素材进仓库/产物库**：`--from-video` 抽帧仅作观察，帧目录放
  临时目录。
- **未过 `--validate` 的设计不交付**给生成层。

## 错误处理

| 现象 | 处理 |
|---|---|
| `--validate` 报衔接断裂 | 修 `transition.entry`（复写上一镜 `exit` 原句） |
| `--validate` 报运镜类型非法 | 用枚举内的类型；复合运镜拆两条或用 `自由`+`path` 描述 |
| PIL 不可用（白模未产出） | 降级交付文字分镜；或在有 PIL 的沙箱重跑 `--example --blockout` |
| `--from-video` ffmpeg 失败 | 检查视频路径/编码；改用已有帧图观察 |
