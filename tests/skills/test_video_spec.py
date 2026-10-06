"""Guardrails for the video-generation skill doc reorg (refactor-df-4).

- old-path grep: pre-reorg reference locations must not be referenced anywhere
  in skills/, backend/{packages,app,tests}, or the root tests/ (runtime data
  like backend/.deer-flow is outside the scan roots by construction).
- narrative existence: each provider folder carries spec / prompt-format /
  examples files.

Routing-table parity against the adapter manifest lands with the manifest
(stage 2/3) in this same file.
"""
import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skills" / "public" / "video-generation"

SCAN_ROOTS = [
    REPO_ROOT / "skills",
    REPO_ROOT / "backend" / "packages",
    REPO_ROOT / "backend" / "app",
    REPO_ROOT / "backend" / "tests",
    REPO_ROOT / "tests",
]
SCAN_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".json", ".ts", ".tsx"}
EXCLUDED_DIR_NAMES = {"__pycache__", "node_modules", ".deer-flow"}
# Directory-level old locations plus renamed files (bare-name pattern catches
# references without the references/ prefix).
OLD_PATTERNS = (
    "references/prompt-format-",
    "prompt-format-seedance",
    "provider-runtime",
    "workflow-examples",
)
# NOTE: bare `prompt-format-base.md` / `prompt-format-ref.md` are intentionally
# NOT guarded — after the reorg the filenames survive inside providers/minimax/,
# and examples.md legitimately references them same-directory. A bare-name hit
# is ambiguous, not stale; the directory-level pattern above covers the stale case.


def _scan_files():
    self_path = Path(__file__).resolve()
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
                continue
            if path == self_path:  # the pattern literals live here
                continue
            yield path


def test_no_references_to_pre_reorg_paths():
    offenders = []
    for path in _scan_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in OLD_PATTERNS:
            if pattern in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {pattern}")
    assert not offenders, "stale pre-reorg paths found:\n" + "\n".join(offenders)


def test_provider_reference_folders_are_complete():
    expected = {
        "minimax": ("spec.md", "prompt-format-base.md", "prompt-format-ref.md", "examples.md"),
        "seedance": ("spec.md", "prompt-format.md", "examples.md"),
        "minimax_h3_sglang": ("spec.md", "prompt-format.md", "examples.md"),
    }
    for provider, files in expected.items():
        for name in files:
            path = SKILL / "references" / "providers" / provider / name
            assert path.is_file(), f"missing references/providers/{provider}/{name}"


def test_reference_paths_pointed_to_by_skill_exist():
    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for token in (
        "references/providers/minimax/prompt-format-base.md",
        "references/providers/minimax/prompt-format-ref.md",
        "references/providers/seedance/prompt-format.md",
        "references/providers/minimax/examples.md",
        "references/providers/seedance/examples.md",
        "references/runtime.md",
        "references/task-lifecycle.md",
    ):
        assert token in skill_text, f"SKILL.md no longer points at {token}"
        assert (SKILL / token).is_file(), f"referenced file missing: {token}"


def _load_skill_providers():
    for name in [m for m in sys.modules if m == "providers" or m.startswith("providers.")]:
        del sys.modules[name]
    scripts = SKILL / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        return importlib.import_module("providers")
    finally:
        sys.path.remove(str(scripts))


def test_routing_table_covers_manifest_prompt_files():
    # forward: every manifest prompt_format_files entry is named in SKILL.md
    # and exists on disk.
    providers = _load_skill_providers()
    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for name, manifest in providers.MANIFESTS.items():
        for mode, rel in manifest.prompt_format_files.items():
            assert rel in skill_text, f"routing table missing {name}/{mode} -> {rel}"
            assert (SKILL / rel).is_file(), f"routing target missing: {rel}"


def test_routing_table_does_not_reference_undeclared_files():
    # reverse: every references/providers/**.md path named in SKILL.md is a
    # declared prompt-format file or a known narrative file
    # (spec/examples/upgrade-2k).
    providers = _load_skill_providers()
    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    declared = {rel for m in providers.MANIFESTS.values() for rel in m.prompt_format_files.values()}
    narrative = {"spec.md", "examples.md", "upgrade-2k.md"}
    undeclared = [
        rel
        for rel in re.findall(r"references/providers/[\w/.-]+\.md", skill_text)
        if rel not in declared and Path(rel).name not in narrative
    ]
    assert not undeclared, f"SKILL.md references undeclared provider docs: {undeclared}"


# --- describe render guards (the zero-copy architecture's only exit) ----------


def _load_skill_generate():
    for name in [m for m in sys.modules if m == "providers" or m.startswith("providers.")]:
        del sys.modules[name]
    scripts = SKILL / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location("video_spec_generate", scripts / "generate.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["video_spec_generate"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(scripts))


def test_describe_renders_every_manifest_model():
    gen = _load_skill_generate()
    providers = _load_skill_providers()
    out = gen.describe_providers(None)
    for name, manifest in providers.MANIFESTS.items():
        assert f"provider: {name}" in out
        for spec in manifest.models:
            assert spec.name in out
            assert f"best for: {spec.description}" in out


def test_describe_degenerate_renders_are_honest():
    gen = _load_skill_generate()
    out = gen.describe_providers("minimax_v1")
    assert "resolution: model defaults" in out
    assert "duration: model defaults" in out
    assert "aspect ratios: not honored (model-side)" in out
    assert "image spec: no local preflight" in out
    assert "image roles: first_frame (implicit — --image-role flag not accepted)" in out


def test_describe_minus_one_auto_annotation_follows_locks():
    gen = _load_skill_generate()
    out = gen.describe_providers("seedance")
    assert "duration: 4-30 (-1 auto)" in out  # 2.5
    assert out.count("(-1 auto)") == 1  # 2.0 family is not annotated


def test_describe_unknown_provider_lists_known_names():
    gen = _load_skill_generate()
    with pytest.raises(ValueError, match="minimax_h3"):
        gen.describe_providers("nope")


def test_describe_rejects_cancel_query_combination():
    result = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "generate.py"), "--describe-provider", "--cancel", "X"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1
    assert "cannot be combined" in result.stderr
    assert "provider: " not in result.stdout
