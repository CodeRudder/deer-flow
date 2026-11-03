#!/usr/bin/env python3
"""storyboard-design 分镜设计 CLI（沙箱内可执行，零第三方依赖）。

纯设计层：输入主题 / 剧本概要文件（/ 参考视频），输出**结构化分镜设计文档**
（JSON + markdown），供 drama-production（MGW drama shots 落库）与
video-clips（MGW storyboard shots 落库）等生成层消费。本脚本不做任何
生成 / HTTP 调用——设计内容（动作三态、运镜、轨迹、占位）由 LLM 逐镜
填写，脚本负责骨架生成、schema 校验与产物落盘。

用法::

    # 从主题/剧本文件出分镜骨架（LLM 逐镜填内容后重跑 --validate）
    python design_storyboard.py \
      --theme "羽毛球对打" --shots 3 \
      --script-file summary.md \
      --out-dir /mnt/user-data/outputs/storyboard/badminton

    # 从参考视频抽帧辅助设计（ffmpeg 抽帧，仅观察素材，不进产物库）
    python design_storyboard.py --from-video /path/ref.mp4 \
      --frame-step 1.0 --frames-dir /tmp/frames

    # 校验已有分镜 JSON（schema 门禁，任何生成层消费前必过）
    python design_storyboard.py --validate path/to/storyboard.json

    # 打印内置羽毛球示例分镜（写法范本，SKILL.md 同款）
    python design_storyboard.py --example

    # 示例 + 顶视白模布局参考图（PIL 几何草图 → out-dir/blockouts/*.png）
    python design_storyboard.py --example --blockout --out-dir /tmp/sbdemo

退出码：0 = 校验通过 / 产物写出；1 = 校验失败或参数错误。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- schema ----

CAMERA_MOVE_TYPES = (
    "固定",  # static lock-off
    "推",  # push in
    "拉",  # pull out
    "摇",  # pan/tilt (机位不动，转角)
    "移",  # truck/dolly lateral
    "跟",  # follow（跟随主体或运动物）
    "升降",  # crane / pedestal
    "环绕",  # orbit
    "手持",  # handheld（含抖动感）
    "自由",  # free-form（在 path 里描述）
)

SHOT_SIZES = ("远景", "全景", "中景", "近景", "特写", "大特写")
VIEWPOINTS = ("客观机位", "POV 主观", "过肩", "无人机俯拍", "贴地低机位")
SPEED_FEEL = ("慢镜", "正常速", "快切", "加速", "减速")

# 每镜必填的顶层键（shot dict）。其余键可选但建议给出。
REQUIRED_SHOT_KEYS = ("index", "action", "camera", "placement")
REQUIRED_ACTION_KEYS = ("before", "during", "after")


def _err(msg: str) -> str:
    return f"[storyboard-design] {msg}"


def validate_storyboard(data: dict) -> list[str]:
    """轻量 schema 校验（零依赖）。返回问题清单，空列表 = 通过。

    校验覆盖用户要求的五组维度：整体设定层 / 动作三态 / 占位与视点 /
    运动物体轨迹 / 镜头衔接（含运镜方法）。
    """
    issues: list[str] = []
    for key in ("title", "scene", "characters"):
        if key not in data:
            issues.append(
                f"缺少整体设定层字段 `{key}`（可为空数组/空串，但键必须存在）"
            )
    shots = data.get("shots")
    if not isinstance(shots, list) or not shots:
        if shots is not None:
            issues.append("`shots` 必须是非空数组")
        return issues

    prev_end: str | None = None
    for shot in shots:
        if not isinstance(shot, dict):
            issues.append("shots[] 中存在非对象项")
            continue
        idx = shot.get("index", "?")
        tag = f"第{idx}镜"

        for key in REQUIRED_SHOT_KEYS:
            if not str(shot.get(key) or "").strip() and not isinstance(
                shot.get(key), dict
            ):
                issues.append(f"{tag} 缺少必填字段 `{key}`")

        # 动作三态
        action = shot.get("action") or {}
        if not isinstance(action, dict):
            issues.append(f"{tag} `action` 必须是对象（before/during/after 三态）")
        else:
            for key in REQUIRED_ACTION_KEYS:
                if not str(action.get(key) or "").strip():
                    issues.append(f"{tag} 动作三态缺 `{key}`（前/中/后）")

        # 占位与视点（主动/被动变化分开标注）
        placement = shot.get("placement")
        if isinstance(placement, dict):
            if not str(placement.get("figure") or "").strip():
                issues.append(
                    f"{tag} `placement.figure` 缺人物占位描述（位置/朝向/占比）"
                )
        else:
            if not str(placement or "").strip():
                issues.append(f"{tag} 缺 `placement`（人物占位/朝向/占比）")
        view = shot.get("viewpoint") or {}
        if isinstance(view, dict):
            if not view.get("shot_size") and not str(view.get("angle") or "").strip():
                issues.append(f"{tag} `viewpoint` 缺景别/角度")
            if not str(view.get("seen") or "").strip():
                issues.append(f"{tag} `viewpoint.seen` 缺“镜头所见”描述")
            if not (
                str(view.get("active_changes") or "").strip()
                or str(view.get("passive_changes") or "").strip()
            ):
                issues.append(
                    f"{tag} 缺主动变化（active_changes）或被动变化（passive_changes）标注"
                )
        else:
            if not str(view or "").strip():
                issues.append(
                    f"{tag} 缺 `viewpoint`（景别/角度/镜头所见/主动被动变化）"
                )

        # 运镜方法
        move = shot.get("camera_movement")
        if isinstance(move, dict):
            mtype = move.get("type")
            if mtype not in CAMERA_MOVE_TYPES:
                issues.append(
                    f"{tag} 运镜类型非法：{mtype!r}（枚举 {','.join(CAMERA_MOVE_TYPES)}；"
                    "自由描述用 `自由` + path）"
                )
            if not str(move.get("path") or "").strip():
                issues.append(
                    f"{tag} `camera_movement.path` 缺运镜路径（起止机位/朝向变化）"
                )
            if not str(move.get("timing") or "").strip():
                issues.append(
                    f"{tag} `camera_movement.timing` 缺运镜时机（与动作三态的同步关系）"
                )
            if not str(move.get("speed") or "").strip():
                issues.append(
                    f"{tag} `camera_movement.speed` 缺运镜速度/节奏（缓/常/急）"
                )
        else:
            issues.append(f"{tag} 缺 `camera_movement`（type/path/timing/speed）")

        # 运动物体轨迹
        for obj in shot.get("motion_objects") or []:
            if not isinstance(obj, dict):
                issues.append(f"{tag} motion_objects[] 存在非对象项")
                continue
            name = obj.get("name", "?")
            traj = obj.get("trajectory") or []
            if not traj:
                issues.append(
                    f"{tag} 运动物 `{name}` 缺 trajectory（起点→高点→落点关键帧）"
                )
            elif isinstance(traj, list):
                for point in traj:
                    if not (
                        isinstance(point, dict) and str(point.get("pos") or "").strip()
                    ):
                        issues.append(f"{tag} 运动物 `{name}` 轨迹关键帧缺 `pos` 描述")
            if not str(obj.get("speed_feel") or "").strip():
                issues.append(
                    f"{tag} 运动物 `{name}` 缺 speed_feel（慢镜/正常速等速度感）"
                )

        # 镜头衔接：上一镜结束姿态 = 下一镜起始姿态
        link = shot.get("transition") or {}
        if isinstance(link, dict):
            if not str(link.get("entry") or "").strip():
                issues.append(f"{tag} `transition.entry` 缺（与上一镜衔接的起始姿态）")
            if not str(link.get("exit") or "").strip():
                issues.append(f"{tag} `transition.exit` 缺（交给下一镜的结束姿态）")

            def _norm(s: str) -> str:
                # 忽略括注（如"（= 镜 1 exit）"）与空白后比较，支持包含关系。
                import re as _re

                s = _re.sub(r"[（(].*?[)）]", "", s)
                return _re.sub(r"\s+", "", s)

            if prev_end is not None:
                a, b = _norm(str(link.get("entry") or "")), _norm(prev_end)
                if a != b and a not in b and b not in a:
                    issues.append(
                        f"{tag} 衔接断裂：`transition.entry`（{link.get('entry')!r}）"
                        f"≠ 上一镜 `transition.exit`（{prev_end!r}）"
                    )
            prev_end = str(link.get("exit") or "").strip() or None
        else:
            issues.append(f"{tag} 缺 `transition`（entry/exit）")

    return issues


def skeleton_storyboard(theme: str, shot_count: int, scene_desc: str = "") -> dict:
    """按主题生成待填骨架（LLM 逐镜填充后再 --validate）。"""
    return {
        "title": theme,
        "logline": "",
        "style": "",
        "scene": scene_desc or f"{theme} 的场景：地点/时间/氛围/光线（待填）",
        "characters": [],
        "props": [],
        "shots": [
            {
                "index": i,
                "title": "",
                "duration_s": 5,
                "action": {"before": "", "during": "", "after": "", "delta": ""},
                "camera": "",
                "camera_movement": {
                    "type": "固定",
                    "path": "",
                    "timing": "",
                    "speed": "常",
                },
                "placement": {"figure": "", "occupancy": ""},
                "viewpoint": {
                    "shot_size": "",
                    "angle": "",
                    "seen": "",
                    "active_changes": "",
                    "passive_changes": "",
                },
                "motion_objects": [],
                "blocking": {
                    # 顶视白模坐标系：x/y ∈ 0–100（0,0=画面/场地左上角）。
                    "characters": [],  # {name, pos:[x,y], facing_deg, label}
                    "camera": {"pos": [50, 95], "target": [50, 50], "move_to": []},
                    "paths": [],  # {name, points:[[x,y],…], times:["0s",…]}
                },
                "blockout": "",  # 白模参考图落盘路径（--blockout 生成后回填）
                "transition": {"entry": "", "exit": ""},
                "prompt_zh": "",
                "prompt_en": "",
                "dialogue": [],
                "refs": [],
            }
            for i in range(1, shot_count + 1)
        ],
    }


# ------------------------------------------------------- example (内置) ----


def example_storyboard() -> dict:
    """羽毛球对打 3 分镜示例（源自 ref-review 羽毛球系列片段的观察提炼）。

    写法范本：动作三态 + 占位/视点（主动/被动变化）+ 运镜 + 球轨迹 +
    跨镜衔接。此 dict 同时被单测引用，必须通过 validate_storyboard。
    """
    return {
        "title": "羽毛球对打：网前一回合",
        "logline": "两名女球员在室内羽毛球馆隔网对打一个回合：发球—平抽—随挥收拍。",
        "style": "写实运动风，明亮均匀顶灯照明，木地板反光，16:9",
        "scene": {
            "location": "室内羽毛球馆，标准绿场地配白色标线",
            "time": "白天，室内顶灯全开",
            "mood": "专注竞技、节奏明快",
            "light": "均匀无影顶光 + 木地板低反射补光，无强侧影",
        },
        "characters": [
            {
                "name": "近景球员（发球方）",
                "appearance": "运动马尾、吸汗发带，运动装",
                "baseline_pose": "双脚前后开立、重心前倾、右手持拍于体前",
                "media_id": "",  # 待资产登记后回填（MGW characters 资产）
                "subject_id": "",  # 引擎一致性绑定号（drama-production 落库用）
            },
            {
                "name": "网对球员（接球方）",
                "appearance": "短发，同系列运动装（撞色）",
                "baseline_pose": "正对来球方向站立、微屈膝、拍头抬起",
                "media_id": "",
                "subject_id": "",
            },
        ],
        "props": [
            {
                "name": "羽毛球拍",
                "notes": "静止道具，跟手持拍；注意拍面反光角度",
            },
            {
                "name": "羽毛球",
                "notes": "运动道具：抛物线轨迹，初速快衰减快（高阻尼），落点前近乎垂直下坠；白色裙状稳定姿态",
            },
            "球网（场地固定物，跨镜不变）",
        ],
        "shots": [
            {
                "index": 1,
                "title": "发球准备与抛球",
                "duration_s": 5,
                "action": {
                    "before": "近景球员背对镜头、侧身持拍，左手捏球举至肩高，网对球员正面微屈膝持拍迎球（帧 badm1-t1.5/t4.0 姿态）",
                    "during": "左手向上抛球，球离开指尖上升至头顶上方约一拍高；持拍手臂后引完成引拍",
                    "after": "拍头尚未前挥——悬停在引拍顶点，本镜止于“即将击球”的瞬间",
                    "delta": "重心从前脚移到后脚再回压；左臂从肩高到完全伸直，变化量约 60cm 位移",
                },
                "camera": "客观机位，中景，稍俯 15°，越过发球方肩头拍网对面",
                "camera_movement": {
                    "type": "推",
                    "path": "自两人侧后方 3m 缓推至 1.8m，机位朝向从“过肩全景”收窄到“持拍侧上半身”",
                    "timing": "抛球瞬间开始推，随引拍完成推近到位（与动作“前→中”同步）",
                    "speed": "缓",
                },
                "placement": {
                    "figure": "发球方占画面左 1/3、背对镜头；接球方占右侧中景、正面；球网横贯中带分隔两人",
                    "occupancy": "两人物各占画面高约 55%（中景），头顶留空 1/4",
                },
                "viewpoint": {
                    "shot_size": "中景",
                    "angle": "稍俯 15°，过肩",
                    "seen": "越过发球方肩头看到网对面接球方全身与球网中线",
                    "active_changes": "发球方抛球+引拍（自身发力），接球方重心下沉预判（主动小位移）",
                    "passive_changes": "球被抛起是被动受抛；画面因推镜两人占比被动变大",
                },
                "motion_objects": [
                    {
                        "name": "羽毛球",
                        "trajectory": [
                            {"phase": "起点", "pos": "发球方左手指尖（肩高）"},
                            {"phase": "高点", "pos": "头顶上方约 60cm，画面上 1/5 处"},
                            {
                                "phase": "落点",
                                "pos": "本镜不落——止于上升段，留给镜 2 接续",
                            },
                        ],
                        "speed_feel": "正常速（抛球段慢于击球段，可轻微减速强调）",
                        "enter_exit": "球从指尖入画，镜末悬在空中不出画（跨镜连续）",
                    }
                ],
                "blocking": {
                    "characters": [
                        {
                            "name": "发球方",
                            "pos": [30, 62],
                            "facing_deg": 0,
                            "label": "A",
                        },
                        {
                            "name": "接球方",
                            "pos": [70, 38],
                            "facing_deg": 180,
                            "label": "B",
                        },
                    ],
                    "camera": {
                        "pos": [30, 88],
                        "target": [70, 38],
                        "move_to": [[45, 82]],
                    },
                    "paths": [
                        {
                            "name": "羽毛球(抛球)",
                            "points": [[30, 62], [30, 52]],
                            "times": ["0s", "2s"],
                        },
                    ],
                },
                "blockout": "",
                "transition": {
                    "entry": "起始接上一场收拍垂拍的放松姿态（本片首镜，取标准准备姿态）",
                    "exit": "发球方引拍顶点悬停、球悬于头顶上方约 60cm",
                },
                "prompt_zh": "室内羽毛球馆，中景稍俯过肩视角。近景女球员背对镜头侧身站立，左手将羽毛球抛向头顶空中约60厘米，右手球拍后引完成引拍悬停在顶点；网对面短发女球员正面微屈膝持拍迎球准备接发。明亮均匀顶灯，木地板，球网横贯画面中线。画面止于球上升瞬间，双方静止蓄势。",
                "prompt_en": "Indoor badminton court, medium shot slightly high-angle over-the-shoulder. Foreground female player, back to camera, tosses a shuttlecock about 60cm above her head with her left hand while her racket arm draws back and pauses at the top of the backswing; on the far side of the net a short-haired female player faces camera, knees bent, racket up, ready to receive. Bright even overhead light, wooden floor, net across mid-frame. Frame ends with the shuttle mid-air, both players coiled.",
                "dialogue": [],
                "refs": [],
            },
            {
                "index": 2,
                "title": "击球瞬间与平抽过网",
                "duration_s": 5,
                "action": {
                    "before": "接镜 1：发球方引拍顶点悬停、球悬在头顶上方",
                    "during": "蹬地转体挥拍击球——拍面触球瞬间（特写接中景），球被击出低平飞过球网",
                    "after": "击球手臂随挥横过身体，肩线从侧对转为正对球网",
                    "delta": "肩转角约 90°；挥拍弧线半径约 1m；球速从 0 到峰值发生在 0.1s 内",
                },
                "camera": "客观机位，中景→近景，与球网平行侧机位",
                "camera_movement": {
                    "type": "跟",
                    "path": "随球过网横向平移（跟随镜头），机位朝向始终锁定球",
                    "timing": "击球瞬间急推近拍面触球特写，球起飞后改横移跟球",
                    "speed": "急（击球段）→ 常（跟球段）",
                },
                "placement": {
                    "figure": "击球后发球方转到画面左 1/3 正面，手臂随挥伸展占据左半幅",
                    "occupancy": "人物占比从中景 55% 升到近景 70%，球网退为背景斜线",
                },
                "viewpoint": {
                    "shot_size": "近景（击球）→ 中景（跟球）",
                    "angle": "与球网等高的侧面平角",
                    "seen": "先看拍面触球细节，再看球低平掠网飞行",
                    "active_changes": "击球方主动发力：蹬地+转体+挥拍+随挥，是全片主动变化峰值",
                    "passive_changes": "球被动受击获得速度；接球方被动进入画面边缘做移位准备；镜头被动跟随球平移",
                },
                "motion_objects": [
                    {
                        "name": "羽毛球",
                        "trajectory": [
                            {
                                "phase": "起点",
                                "pos": "发球方头顶上方约 60cm（接镜 1 出点）",
                            },
                            {"phase": "高点", "pos": "网带上沿上方约 20cm 处掠过"},
                            {
                                "phase": "落点",
                                "pos": "网对面场地前 1/3，画面右下方向飞出",
                            },
                        ],
                        "speed_feel": "击球瞬间可插 2–3 帧慢镜（触球特写），随后正常速跟球",
                        "enter_exit": "球从画面上 1/5 处入画（接上镜），飞向画面右缘接近出画但不切出（留给镜 3）",
                    }
                ],
                "blocking": {
                    "characters": [
                        {
                            "name": "发球方",
                            "pos": [28, 58],
                            "facing_deg": 90,
                            "label": "A",
                        },
                        {
                            "name": "接球方",
                            "pos": [72, 42],
                            "facing_deg": 200,
                            "label": "B",
                        },
                    ],
                    "camera": {
                        "pos": [12, 50],
                        "target": [30, 52],
                        "move_to": [[40, 50], [62, 50]],
                    },
                    "paths": [
                        {
                            "name": "羽毛球(击球过网)",
                            "points": [[28, 52], [50, 44], [78, 46]],
                            "times": ["0s", "1.2s", "2.5s"],
                        },
                    ],
                },
                "blockout": "",
                "transition": {
                    "entry": "发球方引拍顶点悬停、球悬于头顶上方约 60cm（= 镜 1 exit）",
                    "exit": "球飞至画面右缘网对面场地前 1/3 上方、接球方跨步引拍迎球",
                },
                "prompt_zh": "室内羽毛球馆侧面平角镜头。女球员蹬地转体挥拍，拍面触球瞬间球被击出低平飞过球网；镜头先推近拍面触球特写再随球横向平移，球掠过网带上沿飞向网对面，短发女球员跨步引拍迎球。运动张力，明亮顶灯，木地板反光。",
                "prompt_en": "Indoor badminton court, side-level camera at net height. The female player drives off her feet, rotates her torso and swings — racket meets shuttle in a brief contact close-up, then the camera tracks the shuttle laterally as it skims low over the net tape toward the far court, where the short-haired player lunges into a backswing to receive. Athletic tension, bright overhead light, reflective wooden floor.",
                "dialogue": [],
                "refs": [],
            },
            {
                "index": 3,
                "title": "接球落点与随挥收拍",
                "duration_s": 5,
                "action": {
                    "before": "接镜 2：球飞至网对面场地前 1/3 上方，接球方跨步引拍",
                    "during": "接球方正手低点接球轻挡回球，球再次缓慢越过球网（回球弱、球头翻转为减速下坠姿态）",
                    "after": "击球方回位收拍至体前，接球方完成随挥后重心回正，两人进入下一回合准备姿态",
                    "delta": "回球后球速骤降（高阻尼下坠）；双方位移均小于 0.5m，姿态回归镜 1 的准备基线（闭环）",
                },
                "camera": "客观机位，全景，网侧稍高 20°",
                "camera_movement": {
                    "type": "拉",
                    "path": "自近景回拉至两人全景，把网两侧的姿态同时收进画面",
                    "timing": "回球过网瞬间开始拉，随挥完成时拉到全景定住",
                    "speed": "常→缓（收束感）",
                },
                "placement": {
                    "figure": "两人各占画面左右下 1/3，球网中带贯穿，形成对称收束构图（呼应帧 badm1-t9.5 随挥姿态）",
                    "occupancy": "人物各占画面高约 40%（全景），地面标线显出场地纵深",
                },
                "viewpoint": {
                    "shot_size": "全景",
                    "angle": "网侧稍高 20° 俯角",
                    "seen": "同时看到网两侧球员与回球的完整下坠弧线",
                    "active_changes": "接球方主动轻挡回球并完成随挥；击球方主动回位收拍",
                    "passive_changes": "回球被动减速下坠（高阻尼）；画面占比因拉镜被动缩小、场地信息被动展开",
                },
                "motion_objects": [
                    {
                        "name": "羽毛球",
                        "trajectory": [
                            {
                                "phase": "起点",
                                "pos": "接球方拍面（场地前 1/3 低点，接镜 2 出点）",
                            },
                            {
                                "phase": "高点",
                                "pos": "刚过网带上沿即转下坠，高点明显低于镜 2 的轨迹",
                            },
                            {
                                "phase": "落点",
                                "pos": "发球方场地中前区，近乎垂直下坠落地弹起一次",
                            },
                        ],
                        "speed_feel": "减速下坠段建议慢镜强调（羽毛球空气阻尼特征），落点回正常速",
                        "enter_exit": "球从画面左缘入画，落地弹停后留在画内（不切出，回合闭环）",
                    }
                ],
                "blocking": {
                    "characters": [
                        {
                            "name": "发球方",
                            "pos": [32, 60],
                            "facing_deg": 0,
                            "label": "A",
                        },
                        {
                            "name": "接球方",
                            "pos": [68, 44],
                            "facing_deg": 160,
                            "label": "B",
                        },
                    ],
                    "camera": {"pos": [50, 18], "target": [50, 50], "move_to": []},
                    "paths": [
                        {
                            "name": "羽毛球(回球下坠)",
                            "points": [[68, 44], [52, 40], [38, 56], [36, 60]],
                            "times": ["0s", "1s", "2s", "3s"],
                        },
                    ],
                },
                "blockout": "",
                "transition": {
                    "entry": "球飞至画面右缘网对面场地前 1/3 上方、接球方跨步引拍迎球（= 镜 2 exit）",
                    "exit": "双方回归准备姿态、收拍于体前——姿态与镜 1 起始基线一致（可循环）",
                },
                "prompt_zh": "室内羽毛球馆全景，网侧稍高俯角。短发女球员正手低点轻挡回球，羽毛球减速下坠缓慢越过球网落在对面场地中前区；镜头随回球缓缓回拉成全景，两名女球员隔网对称收拍回位，进入下一回合准备姿态。明亮顶灯，木地板，绿场地白标线纵深。",
                "prompt_en": "Indoor badminton court, wide shot from a slightly elevated side angle. The short-haired player blocks the shuttle with a low forehand touch; the shuttle decelerates and drops slowly over the net, landing near-vertical mid-court. The camera eases back into a symmetrical wide as both players reset rackets in front, returning to ready stance for the next rally. Bright overhead light, wooden floor, green court with white lines.",
                "dialogue": [],
                "refs": [],
            },
        ],
    }


# ------------------------------------------------------------- markdown ----


def to_markdown(data: dict) -> str:
    """分镜 JSON → 人读 markdown 分镜板。"""
    lines: list[str] = [f"# 分镜设计：{data.get('title', '')}", ""]
    if data.get("logline"):
        lines += [f"> {data['logline']}", ""]
    if data.get("style"):
        lines += [f"**风格**：{data['style']}", ""]
    scene = data.get("scene")
    lines += ["## 整体设定", ""]
    if isinstance(scene, dict):
        for k in ("location", "time", "mood", "light"):
            if scene.get(k):
                lines.append(f"- **{k}**：{scene[k]}")
    else:
        lines.append(f"- {scene}")
    lines.append("")
    for group, label in (("characters", "角色卡"), ("props", "道具卡")):
        items = data.get(group) or []
        if not items:
            continue
        lines += [f"### {label}", ""]
        for it in items:
            if isinstance(it, dict):
                parts = [f"{k}：{v}" for k, v in it.items() if v and k != "media_id"]
                lines.append(f"- **{it.get('name', '?')}** — " + "；".join(parts))
            else:
                lines.append(f"- {it}")
        lines.append("")

    lines += ["## 分镜板", ""]
    for shot in data.get("shots") or []:
        idx = shot.get("index", "?")
        lines.append(
            f"### 第 {idx} 镜：{shot.get('title', '')}（{shot.get('duration_s', '?')}s）"
        )
        lines.append("")
        act = shot.get("action") or {}
        lines.append(f"- **镜头**：{shot.get('camera', '')}")
        mv = shot.get("camera_movement") or {}
        if mv:
            lines.append(
                f"- **运镜**：{mv.get('type', '')}｜路径：{mv.get('path', '')}"
                f"｜时机：{mv.get('timing', '')}｜速度：{mv.get('speed', '')}"
            )
        lines.append(
            f"- **动作三态**：前——{act.get('before', '')}；中——{act.get('during', '')}；"
            f"后——{act.get('after', '')}。变化量：{act.get('delta', '')}"
        )
        pl = shot.get("placement") or {}
        if isinstance(pl, dict):
            lines.append(
                f"- **人物占位**：{pl.get('figure', '')}（占比：{pl.get('occupancy', '')}）"
            )
        else:
            lines.append(f"- **人物占位**：{pl}")
        vp = shot.get("viewpoint") or {}
        if isinstance(vp, dict):
            lines.append(
                f"- **所见**：{vp.get('shot_size', '')}／{vp.get('angle', '')}｜{vp.get('seen', '')}"
            )
            lines.append(
                f"- **主动变化**：{vp.get('active_changes', '')}／**被动变化**：{vp.get('passive_changes', '')}"
            )
        else:
            lines.append(f"- **所见**：{vp}")
        for obj in shot.get("motion_objects") or []:
            traj = " → ".join(
                f"{p.get('phase', '?')}:{p.get('pos', '')}"
                for p in (obj.get("trajectory") or [])
            )
            lines.append(
                f"- **运动物 {obj.get('name', '?')}**：{traj}｜速度感：{obj.get('speed_feel', '')}"
                f"｜进出画：{obj.get('enter_exit', '')}"
            )
        if shot.get("blockout"):
            lines.append(f"- **白模参考图**：{shot['blockout']}")
        tr = shot.get("transition") or {}
        if tr:
            lines.append(
                f"- **衔接**：入——{tr.get('entry', '')}｜出——{tr.get('exit', '')}"
            )
        for field in ("prompt_zh", "prompt_en"):
            if shot.get(field):
                lines.append(f"- **{field}**：{shot[field]}")
        for d in shot.get("dialogue") or []:
            if isinstance(d, dict):
                lines.append(
                    f"- **台词**：{d.get('speaker', '?')}：{d.get('line', '')}"
                )
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------- video 帧抽取 ----


def extract_frames(video: str, frames_dir: str, step: float) -> list[str]:
    """ffmpeg 等间隔抽帧辅助设计（观察素材，返回帧文件清单）。"""
    os.makedirs(frames_dir, exist_ok=True)
    pattern = os.path.join(frames_dir, "frame-%04d.jpg")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        video,
        "-vf",
        f"fps=1/{max(step, 0.1)}",
        "-q:v",
        "3",
        pattern,
        "-y",
    ]
    subprocess.run(cmd, check=True)
    return sorted(str(p) for p in Path(frames_dir).glob("frame-*.jpg"))


# ------------------------------------------------------ 白模 blockout ----


def render_blockout(
    shot: dict, out_path: str, width: int = 960, height: int = 540
) -> bool:
    """顶视 2D 白模布局草图（几何占位，非 AI 生图；不依赖网关）。

    画：场地边界、角色圆点+编号+朝向箭头、道具/球轨迹折线+时间标注、
    机位十字+运镜箭头。坐标系 shot.blocking：x/y ∈ 0–100。
    PIL 不可用时返回 False（调用方降级提示，不致命）。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False

    img = Image.new("RGB", (width, height), "#f5f5f0")
    draw = ImageDraw.Draw(img)

    def xy(p):
        return (p[0] / 100.0 * (width - 40) + 20, p[1] / 100.0 * (height - 40) + 20)

    def font(size):
        for name in ("NotoSansCJK-Regular.ttc", "wqy-microhei.ttc", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    f_label, f_small = font(18), font(13)

    # 场地边界（外框 + 中线示意网）
    draw.rectangle([20, 20, width - 20, height - 20], outline="#888888", width=3)
    draw.line([width // 2, 20, width // 2, height - 20], fill="#4a7c59", width=4)

    bl = shot.get("blocking") or {}
    for ch in bl.get("characters") or []:
        x, y = xy(ch.get("pos", [50, 50]))
        label = str(ch.get("label") or "?")
        draw.ellipse(
            [x - 16, y - 16, x + 16, y + 16], fill="#3b6ea5", outline="#1d3f63", width=2
        )
        draw.text((x - 6, y - 10), label, fill="white", font=f_label)
        ang = float(ch.get("facing_deg") or 0)
        import math

        dx, dy = math.sin(math.radians(ang)), -math.cos(math.radians(ang))
        draw.line(
            [x + dx * 18, y + dy * 18, x + dx * 42, y + dy * 42],
            fill="#c0392b",
            width=4,
        )
        name = str(ch.get("name") or "")
        if name:
            draw.text((x + 20, y + 18), name, fill="#333333", font=f_small)

    for path in bl.get("paths") or []:
        pts = [xy(p) for p in (path.get("points") or [])]
        if not pts:
            continue
        color = "#d35400" if "球" in str(path.get("name", "")) else "#7d3c98"
        draw.line(pts, fill=color, width=3)
        for i, (px, py) in enumerate(pts):
            draw.ellipse([px - 4, py - 4, px + 4, py + 4], fill=color)
            times = path.get("times") or []
            if i < len(times):
                draw.text((px + 6, py - 16), str(times[i]), fill=color, font=f_small)

    cam = bl.get("camera") or {}
    if cam:
        cx, cy = xy(cam.get("pos", [50, 95]))
        tx, ty = xy(cam.get("target", [50, 50]))
        draw.line([cx, cy, tx, ty], fill="#16a085", width=2)
        draw.line([cx - 10, cy, cx + 10, cy], fill="#16a085", width=3)
        draw.line([cx, cy - 10, cx, cy + 10], fill="#16a085", width=3)
        draw.text((cx + 12, cy), "CAM", fill="#16a085", font=f_small)
        for p in cam.get("move_to") or []:
            mx, my = xy(p)
            draw.line([cx, cy, mx, my], fill="#16a085", width=2)
            draw.ellipse([mx - 5, my - 5, mx + 5, my + 5], outline="#16a085", width=2)
            cx, cy = mx, my

    title = f"白模 · 第{shot.get('index', '?')}镜 {shot.get('title', '')}"
    draw.text((26, 4), title, fill="#222222", font=f_label)
    img.save(out_path, "PNG")
    return True


def render_blockouts(data: dict, out_dir: Path, only_key: bool = False) -> list[str]:
    """逐镜渲染白模并把路径回填进 shot.blockout。返回成功清单。"""
    written: list[str] = []
    bl_dir = out_dir / "blockouts"
    bl_dir.mkdir(parents=True, exist_ok=True)
    for shot in data.get("shots") or []:
        idx = shot.get("index")
        if idx is None or (only_key and not shot.get("key_shot")):
            continue
        path = str(bl_dir / f"shot{int(idx):02d}-blockout.png")
        if render_blockout(shot, path):
            shot["blockout"] = path
            written.append(path)
        else:
            shot["blockout"] = ""
    return written


# ------------------------------------------------------------------ main ----


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="storyboard-design 分镜设计 CLI")
    parser.add_argument("--theme", help="主题一句话（骨架生成模式）")
    parser.add_argument("--shots", type=int, default=3, help="分镜数量（缺省 3）")
    parser.add_argument(
        "--script-file", help="剧本/概要 markdown 文件（内容并入 meta.script）"
    )
    parser.add_argument("--scene", default="", help="场景一句话描述")
    parser.add_argument("--out-dir", default="/mnt/user-data/outputs/storyboard/design")
    parser.add_argument(
        "--from-video", help="参考视频：抽帧辅助设计（不调任何生成 API）"
    )
    parser.add_argument(
        "--frame-step", type=float, default=1.0, help="抽帧间隔秒（缺省 1.0）"
    )
    parser.add_argument(
        "--frames-dir", default="", help="抽帧输出目录（缺省 out-dir/frames）"
    )
    parser.add_argument(
        "--validate", metavar="JSON", help="校验已有分镜 JSON，通过打印 OK"
    )
    parser.add_argument(
        "--example", action="store_true", help="打印内置羽毛球示例分镜 JSON"
    )
    parser.add_argument(
        "--blockout",
        action="store_true",
        help="为每个 shot 渲染顶视白模布局参考图（PIL 几何草图，需 blocking 数据）",
    )
    parser.add_argument(
        "--blockout-key-only",
        action="store_true",
        help="只为标记 key_shot 的镜渲染白模",
    )
    args = parser.parse_args(argv)

    if args.example:
        data = example_storyboard()
        if args.blockout:
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            written = render_blockouts(data, out_dir)
            json_path = out_dir / "badminton-example.storyboard.json"
            md_path = out_dir / "badminton-example.storyboard.md"
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            md_path.write_text(to_markdown(data), encoding="utf-8")
            print(
                json.dumps(
                    {"json": str(json_path), "md": str(md_path), "blockouts": written},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if written else (print(_err("PIL 不可用，白模未生成")) or 1)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if args.validate:
        data = json.loads(Path(args.validate).read_text(encoding="utf-8"))
        issues = validate_storyboard(data)
        if issues:
            print(_err("schema 校验未通过："))
            for i in issues:
                print(f"  - {i}")
            return 1
        print(_err("schema 校验通过 ✓"))
        return 0

    if args.from_video:
        out_dir = Path(args.out_dir)
        frames_dir = args.frames_dir or str(out_dir / "frames")
        frames = extract_frames(args.from_video, frames_dir, args.frame_step)
        print(_err(f"抽帧 {len(frames)} 张 → {frames_dir}"))
        print(_err("下一步：逐帧观察提炼动作三态/运镜/轨迹，逐镜填充骨架后 --validate"))
        return 0

    if not args.theme:
        parser.error("需要 --theme（或 --example / --validate / --from-video）")
        return 1

    data = skeleton_storyboard(args.theme, max(1, min(args.shots, 20)), args.scene)
    if args.script_file:
        data["script_summary"] = Path(args.script_file).read_text(encoding="utf-8")

    issues = validate_storyboard(data)
    if issues:
        # 骨架必然未填——打印待填清单作为 checklist，但骨架模式不判失败。
        print(_err(f"骨架已生成，尚有 {len(issues)} 项待 LLM 填充："))
        for i in issues[:20]:
            print(f"  - {i}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.blockout:
        written = render_blockouts(data, out_dir, only_key=args.blockout_key_only)
        print(
            _err(
                f"白模 {len(written)} 张 → {out_dir / 'blockouts'}"
                + ("" if written else "（PIL 不可用或无 blocking 数据）")
            )
        )
    stem = re.sub(r"[\s/\\]+", "-", args.theme.strip()).strip("-.")[:48] or "storyboard"
    json_path = out_dir / f"{stem}.storyboard.json"
    md_path = out_dir / f"{stem}.storyboard.md"
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(to_markdown(data), encoding="utf-8")
    print(_err(f"骨架写出：{json_path}"))
    print(_err(f"       {md_path}"))
    print(
        json.dumps(
            {"json": str(json_path), "md": str(md_path), "pending_items": len(issues)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
