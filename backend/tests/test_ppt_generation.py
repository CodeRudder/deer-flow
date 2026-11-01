import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "skills" / "public" / "ppt-generation" / "scripts"


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


generate_module = _load_module("ppt_generation_generate", SCRIPT_DIR / "generate.py")


def test_generate_ppt_refuses_to_overwrite_existing_output(tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text('{"aspect_ratio": "16:9", "slides": []}', encoding="utf-8")
    output_file = tmp_path / "presentation.pptx"
    output_file.write_bytes(b"existing-presentation")

    with pytest.raises(FileExistsError) as exc:
        generate_module.generate_ppt(
            plan_file=str(plan_file),
            slide_images=[],
            output_file=str(output_file),
        )

    assert "will not be overwritten" in str(exc.value)
    assert output_file.read_bytes() == b"existing-presentation"


def test_generate_ppt_can_overwrite_when_explicitly_requested(tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text('{"aspect_ratio": "16:9", "slides": []}', encoding="utf-8")
    output_file = tmp_path / "presentation.pptx"
    output_file.write_bytes(b"existing-presentation")

    result = generate_module.generate_ppt(
        plan_file=str(plan_file),
        slide_images=[],
        output_file=str(output_file),
        overwrite=True,
    )

    assert "Successfully generated presentation with 0 slides" in result
    assert output_file.read_bytes() != b"existing-presentation"
