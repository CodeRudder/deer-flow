"""Issue #3647 — LocalSandbox must compile its path-rewrite regexes once per
sandbox (cached), not on every bash/read_file/write_file call, while keeping
the exact same rewriting behavior.
"""

import os
import shlex
from pathlib import Path

from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping


def _make_sandbox(tmp_path: Path) -> LocalSandbox:
    ws = tmp_path / "workspace"
    skills = tmp_path / "skills"
    ws.mkdir()
    skills.mkdir()
    return LocalSandbox(
        id="test",
        path_mappings=[
            PathMapping(container_path="/mnt/user-data/workspace", local_path=str(ws)),
            PathMapping(container_path="/mnt/skills", local_path=str(skills), read_only=True),
        ],
    )


def test_patterns_are_compiled_once_and_cached(tmp_path):
    sb = _make_sandbox(tmp_path)
    # Each cached_property returns the identical object across accesses.
    assert sb._command_pattern is sb._command_pattern
    assert sb._content_pattern is sb._content_pattern
    assert sb._reverse_output_patterns is sb._reverse_output_patterns
    # Two mappings -> two reverse-output patterns.
    # On Windows each mapping emits two separator variants (backslash root for
    # command output + forward-slash root for agent-written content), so the
    # list doubles there; POSIX roots have no backslashes and dedupe to one.
    expected_per_mapping = 2 if os.name == "nt" else 1
    assert len(sb._reverse_output_patterns) == 2 * expected_per_mapping


def test_empty_mappings_yield_no_pattern(tmp_path):
    sb = LocalSandbox(id="empty", path_mappings=[])
    assert sb._command_pattern is None
    assert sb._content_pattern is None
    assert sb._reverse_output_patterns == []
    # No mappings -> command/content pass through unchanged.
    assert sb._resolve_paths_in_command("echo hello") == "echo hello"
    assert sb._resolve_paths_in_content("plain text") == "plain text"


def test_command_paths_resolved_to_local(tmp_path):
    sb = _make_sandbox(tmp_path)
    ws_local = str((tmp_path / "workspace").resolve())
    out = sb._resolve_paths_in_command("cat /mnt/user-data/workspace/foo.txt")
    # Normalize separators for cross-platform comparison (Windows uses \, the
    # f-string appends /foo.txt, producing mixed separators that don't match
    # the fully-resolved output).
    assert out.replace("\\", "/") == f"cat {ws_local}/foo.txt".replace("\\", "/")
    # Calling again uses the cached pattern and produces the same result.
    assert sb._resolve_paths_in_command("cat /mnt/user-data/workspace/foo.txt") == out


def test_command_paths_resolved_to_local_are_shell_safe_on_windows(tmp_path):
    """Resolved command paths must be shell-safe (forward slashes) on Windows.

    Custom-mount container paths are rewritten by ``_resolve_paths_in_command``
    rather than ``replace_virtual_paths_in_command``, so the forward-slash
    normalization must happen here too — otherwise an unquoted ``D:\\code\\x``
    is flattened to ``D:codex`` by MSYS ``sh`` / ``shlex``. This mirrors what
    ``_resolve_paths_in_content`` already does for file content.
    """
    mount = tmp_path / "data"
    (mount / "sub").mkdir(parents=True)
    sb = LocalSandbox(
        id="test",
        path_mappings=[PathMapping(container_path="/mnt/data", local_path=str(mount))],
    )
    resolved = sb._resolve_paths_in_command("cat /mnt/data/sub/file.txt")

    # No backslashes anywhere: the path must survive POSIX shell tokenization.
    assert "\\" not in resolved, resolved
    # Concretely, shlex must keep the path as a single token instead of
    # collapsing it to garbage.
    expected_target = str(mount).replace("\\", "/") + "/sub/file.txt"
    assert shlex.split(resolved) == ["cat", expected_target]


def test_segment_boundary_not_matched_inside_longer_name(tmp_path):
    sb = _make_sandbox(tmp_path)
    # "/mnt/skills-extra" must NOT be rewritten by the "/mnt/skills" mapping.
    out = sb._resolve_paths_in_command("ls /mnt/skills-extra/data")
    assert out == "ls /mnt/skills-extra/data"


def test_reverse_resolve_output_maps_local_back_to_container(tmp_path):
    sb = _make_sandbox(tmp_path)
    ws_local = str((tmp_path / "workspace").resolve())
    out = sb._reverse_resolve_paths_in_output(f"wrote {ws_local}/foo.txt ok")
    assert out == "wrote /mnt/user-data/workspace/foo.txt ok"


def test_resolved_paths_and_sorted_views_are_cached(tmp_path):
    sb = _make_sandbox(tmp_path)
    # Resolved-local map and sorted views are computed once and reused.
    assert sb._resolved_local_paths is sb._resolved_local_paths
    assert sb._mappings_by_container_specificity is sb._mappings_by_container_specificity
    assert sb._mappings_by_local_specificity is sb._mappings_by_local_specificity
    # Map covers every mapping with its filesystem-resolved local root.
    assert set(sb._resolved_local_paths.values()) == {
        str((tmp_path / "workspace").resolve()),
        str((tmp_path / "skills").resolve()),
    }
    # Most-specific (longest) container path is ordered first.
    assert sb._mappings_by_container_specificity[0].container_path == "/mnt/user-data/workspace"


def test_forward_resolution_behavior_unchanged(tmp_path):
    sb = _make_sandbox(tmp_path)
    ws_local = str((tmp_path / "workspace").resolve())
    # Container path resolves to the mapped local path.
    resolved = sb._resolve_path("/mnt/user-data/workspace/sub/foo.txt")
    assert resolved.replace("\\", "/") == f"{ws_local}/sub/foo.txt".replace("\\", "/")
    # An unmapped path is returned unchanged.
    assert sb._resolve_path("/etc/hosts") == "/etc/hosts"


def test_read_only_mount_detected(tmp_path):
    sb = _make_sandbox(tmp_path)
    skills_local = str((tmp_path / "skills").resolve())
    ws_local = str((tmp_path / "workspace").resolve())
    assert sb._is_read_only_path(f"{skills_local}/a.md") is True
    assert sb._is_read_only_path(f"{ws_local}/a.txt") is False
