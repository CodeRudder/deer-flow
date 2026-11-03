"""storyboard-design skill tests: schema validation, example integrity, CLI smoke."""

import importlib.util
import itertools
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    REPO_ROOT
    / "skills"
    / "public"
    / "storyboard-design"
    / "scripts"
    / "design_storyboard.py"
)

spec = importlib.util.spec_from_file_location("design_storyboard", SCRIPT)
sbd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sbd)


def test_example_passes_schema():
    issues = sbd.validate_storyboard(sbd.example_storyboard())
    assert issues == [], f"内置示例必须过 schema 校验，问题：{issues}"


def test_example_covers_required_dimensions():
    data = sbd.example_storyboard()
    assert len(data["shots"]) == 3
    assert data["characters"] and data["props"]
    for shot in data["shots"]:
        # 动作三态
        for key in ("before", "during", "after"):
            assert shot["action"][key].strip()
        # 占位与视点，主动/被动变化分开
        assert shot["viewpoint"]["active_changes"].strip()
        assert shot["viewpoint"]["passive_changes"].strip()
        # 运镜四要素
        assert shot["camera_movement"]["type"] in sbd.CAMERA_MOVE_TYPES
        for key in ("path", "timing", "speed"):
            assert shot["camera_movement"][key].strip()
        # 运动物轨迹三关键帧 + 速度感
        for obj in shot["motion_objects"]:
            assert len(obj["trajectory"]) >= 3
            assert obj["speed_feel"].strip()
        # 衔接 + 白模 blocking
        assert shot["transition"]["entry"] and shot["transition"]["exit"]
        assert shot["blocking"]["characters"]


def test_cross_shot_continuity():
    shots = sbd.example_storyboard()["shots"]
    for prev, cur in itertools.pairwise(shots):
        assert (
            prev["transition"]["exit"]
            == cur["transition"]["entry"].split("（")[0].strip()
            or cur["transition"]["entry"] in prev["transition"]["exit"]
            or prev["transition"]["exit"] in cur["transition"]["entry"]
        ), f"第{cur['index']}镜衔接与上一镜出点不连续"


def test_validate_flags_breakage():
    data = sbd.example_storyboard()
    data["shots"][1]["action"].pop("during")
    data["shots"][2]["camera_movement"]["type"] = "甩镜"
    data["shots"][2]["transition"] = {"entry": "随便一个别的姿态", "exit": ""}
    issues = sbd.validate_storyboard(data)
    joined = "\n".join(issues)
    assert "during" in joined
    assert "运镜类型非法" in joined
    assert "衔接断裂" in joined
    assert "transition.exit" in joined


def test_skeleton_lists_pending_items():
    data = sbd.skeleton_storyboard("测试主题", 2)
    issues = sbd.validate_storyboard(data)
    assert issues, "骨架必然未填，必须列出待填清单"
    assert any("动作三态" in i for i in issues)


def test_markdown_render(tmp_path):
    md = sbd.to_markdown(sbd.example_storyboard())
    assert "分镜设计：羽毛球对打" in md
    assert "主动变化" in md and "被动变化" in md
    assert "运镜" in md and "白模参考图" not in md  # blockout 未回填时不出现在 md


def test_blockout_render(tmp_path):
    try:
        import PIL  # noqa: F401
    except ImportError:
        import pytest

        pytest.skip("PIL 不可用，白模渲染跳过")
    out = tmp_path / "shot01-blockout.png"
    ok = sbd.render_blockout(sbd.example_storyboard()["shots"][0], str(out))
    assert ok and out.exists() and out.stat().st_size > 1000


def test_cli_example_and_validate(tmp_path):
    out_dir = tmp_path / "demo"
    r = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--example",
            "--blockout",
            "--out-dir",
            str(out_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    json_path = Path(payload["json"])
    assert json_path.exists() and Path(payload["md"]).exists()
    assert len(payload["blockouts"]) == 3

    r2 = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--validate", str(json_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert r2.returncode == 0 and "通过" in r2.stdout, r2.stdout + r2.stderr


def test_cli_skeleton(tmp_path):
    out_dir = tmp_path / "skel"
    r = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--theme",
            "羽毛球对打",
            "--shots",
            "3",
            "--out-dir",
            str(out_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert Path(payload["json"]).exists() and payload["pending_items"] > 0
