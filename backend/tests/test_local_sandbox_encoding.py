import builtins
from types import SimpleNamespace

import deerflow.sandbox.local.local_sandbox as local_sandbox
from deerflow.sandbox.local.local_sandbox import LocalSandbox


def _open(base, file, mode="r", *args, **kwargs):
    if "b" in mode:
        return base(file, mode, *args, **kwargs)
    return base(file, mode, *args, encoding=kwargs.pop("encoding", "gbk"), **kwargs)


def test_read_file_uses_utf8_on_windows_locale(tmp_path, monkeypatch):
    path = tmp_path / "utf8.txt"
    text = "\u201cutf8\u201d"
    path.write_text(text, encoding="utf-8")
    base = builtins.open

    monkeypatch.setattr(local_sandbox, "open", lambda file, mode="r", *args, **kwargs: _open(base, file, mode, *args, **kwargs), raising=False)

    assert LocalSandbox("t").read_file(str(path)) == text


def test_write_file_uses_utf8_on_windows_locale(tmp_path, monkeypatch):
    path = tmp_path / "utf8.txt"
    text = "emoji \U0001f600"
    base = builtins.open

    monkeypatch.setattr(local_sandbox, "open", lambda file, mode="r", *args, **kwargs: _open(base, file, mode, *args, **kwargs), raising=False)

    LocalSandbox("t").write_file(str(path), text)

    assert path.read_text(encoding="utf-8") == text


def test_get_shell_prefers_posix_shell_from_path_before_windows_fallback(monkeypatch):
    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_find_first_available_shell", lambda candidates: r"C:\Program Files\Git\bin\sh.exe" if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh") else None)

    assert LocalSandbox._get_shell() == r"C:\Program Files\Git\bin\sh.exe"


def test_get_shell_uses_powershell_fallback_on_windows(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_find(candidates: tuple[str, ...]) -> str | None:
        calls.append(candidates)
        if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh"):
            return None
        return r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"SystemRoot": r"C:\Windows"})
    monkeypatch.setattr(LocalSandbox, "_find_first_available_shell", fake_find)

    assert LocalSandbox._get_shell() == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    assert calls[1] == (
        "pwsh",
        "pwsh.exe",
        "powershell",
        "powershell.exe",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "cmd.exe",
    )


def test_get_shell_uses_cmd_as_last_windows_fallback(monkeypatch):
    def fake_find(candidates: tuple[str, ...]) -> str | None:
        if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh"):
            return None
        return r"C:\Windows\System32\cmd.exe"

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"SystemRoot": r"C:\Windows"})
    monkeypatch.setattr(LocalSandbox, "_find_first_available_shell", fake_find)

    assert LocalSandbox._get_shell() == r"C:\Windows\System32\cmd.exe"


def test_execute_command_uses_powershell_command_mode_on_windows(monkeypatch):
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    output = LocalSandbox("t").execute_command("Write-Output hello")

    assert output == "ok"
    assert calls == [
        (
            [
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile",
                "-Command",
                "Write-Output hello",
            ],
            {
                "shell": False,
                "capture_output": True,
                "text": True,
                "timeout": 600,
                "env": None,
            },
        )
    ]


def test_execute_command_uses_posix_shell_command_mode_on_windows(monkeypatch):
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"PATH": r"C:\Program Files\Git\bin"})
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Program Files\Git\bin\sh.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    output = LocalSandbox("t").execute_command("echo hello")

    assert output == "ok"
    assert calls == [
        (
            [r"C:\Program Files\Git\bin\sh.exe", "-c", "echo hello"],
            {
                "shell": False,
                "capture_output": True,
                "text": True,
                "timeout": 600,
                "env": {
                    "PATH": r"C:\Program Files\Git\bin",
                    "MSYS_NO_PATHCONV": "1",
                    "MSYS2_ARG_CONV_EXCL": "*",
                },
            },
        )
    ]


def test_execute_command_does_not_set_msys_env_for_non_msys_posix_shell_on_windows(monkeypatch):
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\tools\busybox\sh.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    output = LocalSandbox("t").execute_command("echo /mnt/skills/demo")

    assert output == "ok"
    assert calls[0][1]["env"] is None


def test_execute_command_joins_bash_line_continuations_for_powershell(monkeypatch):
    """PowerShell/cmd do not understand bash ``\\``+newline line continuations.

    The agent emits multi-line commands using bash-style ``\\`` continuation
    (e.g. the image-generation SKILL.md example). PowerShell parses ``\\`` as a
    literal and ``--flag`` as its own operator, so the whole command breaks.
    ``execute_command`` must join those continuations into a single line before
    handing the command to a non-POSIX Windows shell.
    """
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    multi_line = "python /mnt/skills/public/image-generation/scripts/generate.py \\\n  --prompt-file /mnt/user-data/workspace/p.json \\\n  --output-file /mnt/user-data/outputs/o.png"
    LocalSandbox("t").execute_command(multi_line)

    sent_command = calls[0][0][3]
    assert "\\\n" not in sent_command
    assert "\\\r\n" not in sent_command
    assert sent_command == "python /mnt/skills/public/image-generation/scripts/generate.py    --prompt-file /mnt/user-data/workspace/p.json    --output-file /mnt/user-data/outputs/o.png"


def test_execute_command_preserves_continuations_for_posix_shell_on_windows(monkeypatch):
    """POSIX shells (Git Bash/MSYS) understand ``\\`` continuation, so it must stay."""
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Program Files\Git\bin\sh.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    multi_line = "echo hello \\\nworld"
    LocalSandbox("t").execute_command(multi_line)

    sent_command = calls[0][0][2]
    assert "\\\n" in sent_command


def test_execute_command_joins_bash_line_continuations_for_cmd(monkeypatch):
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\cmd.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    LocalSandbox("t").execute_command("echo a \\\n  b \\\n  c")

    sent_command = calls[0][0][2]
    assert "\\\n" not in sent_command


def test_execute_command_joins_bash_continuations_with_crlf(monkeypatch):
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    LocalSandbox("t").execute_command("echo a \\\r\n  b")

    sent_command = calls[0][0][3]
    assert "\\\r\n" not in sent_command
    assert "\\\n" not in sent_command


def test_execute_command_does_not_touch_single_backslash_in_powershell(monkeypatch):
    """A lone backslash that is NOT a line continuation (no trailing newline) must stay intact."""
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    LocalSandbox("t").execute_command(r"Write-Output 'a\b'")

    sent_command = calls[0][0][3]
    assert sent_command == r"Write-Output 'a\b'"


def test_execute_command_uses_cmd_command_mode_on_windows(monkeypatch):
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\cmd.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    output = LocalSandbox("t").execute_command("echo hello")

    assert output == "ok"
    assert calls == [
        (
            [r"C:\Windows\System32\cmd.exe", "/c", "echo hello"],
            {
                "shell": False,
                "capture_output": True,
                "text": True,
                "timeout": 600,
                "env": None,
            },
        )
    ]


def test_resolve_paths_in_command_is_identity_without_mappings():
    """No path mappings => no replacement => command returned unchanged."""
    sandbox = LocalSandbox(id="empty", path_mappings=[])
    assert sandbox._resolve_paths_in_command("python /mnt/skills/x.py") == "python /mnt/skills/x.py"
