# storyboard-design（分镜设计）Skill 任务书

日期：2026-11-02
状态：实施中（子代理执行）
定位：**纯设计层** skill——输入主题/剧本概要/参考视频，输出结构化分镜设计（JSON+Markdown），供 drama-production / video-clips 生成层消费；自身不做生成执行。

## 一、背景与参考素材

用户要求以**羽毛球视频片段**为参考范式构建分镜设计能力。素材位于 `/home/nvidia/h3img-artifacts/ref-review/`（badminton-shuttle-v3.mp4、badminton-5plus-closeup.mp4、badm1-t*.jpg 帧图、badminton-comic-p* 等）。素材仅用于提炼镜头语言写法与内置示例，**不拷入仓库**。

## 二、分镜 Schema（每个 shot 一份结构）

| 维度 | 字段要求 |
|---|---|
| 整体设定 | 场景（地点/时间/氛围/光线）；角色卡（外观/服装/姿势基线，引用角色资产 media_id）；道具卡（运动道具特殊标注） |
| 动作三态 | 角色（含持道具肢体）动作的 **前（准备姿态）/中（运动瞬间）/后（随挥/落点）** 三段细节与变化量 |
| 占位与视点 | 人物画面位置/朝向/占比（blocking）；镜头所见（POV/客观机位/景别/角度） |
| 主/被动变化 | **主动变化**（自身移动/转体/发力）与**被动变化**（被击打/被带动/镜头跟随产生的画面变化）分开标注 |
| 运动物体轨迹 | 球/抛掷物关键帧轨迹（起点→高点→落点）、速度感（慢镜/常速）、跨 shot 连续性、遮挡进出画处理 |
| 镜头衔接 | 上一 shot 结束姿态 = 下一 shot 起始姿态（衔接点显式声明） |
| **运镜方法** | 类型枚举（推/拉/摇/移/跟/升降/环绕/手持抖动/固定）+ 自由描述；运镜路径（起止机位/朝向变化）；**运镜时机与动作三态同步**（如击球瞬间推近、随挥后拉远）；速度节奏（缓/常/急） |

## 三、白模参考图辅助（可选，按需开启）

- CLI 开关 `--blockout`，默认关闭。
- 为每个（或关键）shot 生成**白模布局参考图**：PIL 矢量绘制（几何占位草图，非 AI 生图）——顶视 2D：场地边界、角色圆点+编号+朝向箭头、球/道具轨迹线（贝塞尔/折线+时间标注）、机位与运镜路径箭头。
- 白模落盘产物目录，shot JSON 引用其路径；白模元素与 schema 的 trajectory/blocking/运镜字段**一一对应**。
- 用途（写入 SKILL.md）：为生成层（t2v/i2v 首帧、角色参考）提供位置/轨迹的确定性约束，与文字描述互补。

## 四、交付物

| 项 | 内容 |
|---|---|
| `skills/public/storyboard-design/SKILL.md` | 触发词（分镜设计/分镜/storyboard）；流程；schema 说明；运镜术语速查表；羽毛球示例分镜 ≥2 条（含运镜标注）；边界声明（生成执行引导至 drama-production/video-clips） |
| `skills/public/storyboard-design/scripts/design_storyboard.py` | 沙箱 CLI（同 t2i-series 模式，零第三方依赖，PIL 可用）：输入主题/剧本文件 → 分镜 JSON+Markdown 到产物目录；`--from-video` 对参考视频 ffmpeg 抽帧辅助设计；`--blockout` 白模开关 |
| Schema 校验 | 内置轻量校验函数 + 单测（按仓库测试惯例），白模元素与字段映射断言 |

## 五、契约对齐

- shots 字段对齐 drama 落库契约（backend drama shots 代码 + 网关 `docs/integration/api/drama.md`）；差异处注释标注映射，**不改后端**。
- 与 drama-production（五要素）/video-clips 的分工：本 skill 产出更细粒度设计（三态/运镜/轨迹/白模），其输出可被两者消费。

## 六、验收标准

1. CLI 对「羽毛球对打 3 分镜」样例输入产出完整 JSON+Markdown，自检通过；带 `--blockout` 再跑一次，白模图产出且元素与 JSON 字段对应
2. SKILL.md 示例与 schema 一致；运镜术语表齐备
3. 单测全绿（校验/白模映射/CLI 主流程）
4. 触发词路由验证：会话中"分镜设计"命中本 skill，"生成视频"仍路由到生成类 skill

## 七、验收状态

- [x] 完成（提交 `529681f9`，2026-11-02）：SKILL.md+CLI+9 例单测全绿；羽毛球 3 分镜示例 `--example --blockout` 实跑产出 JSON/md/3 张白模 PNG（VLM 目检场地/朝向/轨迹/运镜线齐全）→ `--validate` 通过；运镜 10 类枚举+三态同步时机；白模 `--blockout/--blockout-key-only`；落库映射表（三态→prompt_zh、运镜→camera 等，未改后端）
- [ ] 会话触发词路由验证（分镜设计命中本 skill）——待 dev 会话实测
