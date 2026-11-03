"""Media skill delivery-contract tests.

Background (2026-11-03, 《深圳湾日出》 thread 3b897ad6): a drama-production
run completed its MGW video job (img_96b448571d5b, status=completed) but the
UI showed neither a video card nor a usable job URL — the skills only told the
agent to "report the proxy path as text", so the video bytes were never written
into `/mnt/user-data/outputs/` and `present_files` / ArtifactAutoPresent
middleware had nothing to register. These tests pin the delivery contract that
fixes that: MGW-driven media skills MUST land the bytes in outputs, call
present_files, and report job_id + absolute external proxy URL links (never
inside code blocks) plus raw job status on failure.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Each entry: skill -> (delivery-section anchor, download env vars the step must reference).
MEDIA_SKILLS = {
    "drama-production": ("阶段 4", ["$MEDIA_GATEWAY_BASE_URL", "$MEDIA_GATEWAY_TOKEN"]),
    "video-clips": ("⑥", ["$MEDIA_GATEWAY_BASE_URL", "$MEDIA_GATEWAY_TOKEN"]),
}

OUTPUTS_PATH = "/mnt/user-data/outputs/"


def _skill_md(name: str) -> str:
    return (REPO_ROOT / "skills" / "public" / name / "SKILL.md").read_text(encoding="utf-8")


def _delivery_section(text: str, anchor: str) -> str:
    idx = text.index(anchor)
    return text[idx:]


def test_delivery_contract_outputs_landing_and_present_files():
    """Video bytes must land in /mnt/user-data/outputs and be presented."""
    for name, (anchor, _) in MEDIA_SKILLS.items():
        section = _delivery_section(_skill_md(name), anchor)
        assert OUTPUTS_PATH in section, f"{name}: 交付段必须要求落盘 {OUTPUTS_PATH}"
        assert ".mp4" in section, f"{name}: 交付段必须指明 mp4 落盘文件"
        assert "present_files" in section, f"{name}: 交付段必须要求调用 present_files"
        # The rule that makes the failure mode explicit, so future edits keep it.
        assert "不算交付" in section, f"{name}: 必须写明只给文本 URL 不算交付"


def test_delivery_contract_download_uses_sandbox_mgw_credentials():
    """Downloads use the sandbox-injected MGW env (internal base url + token)."""
    for name, (anchor, env_vars) in MEDIA_SKILLS.items():
        section = _delivery_section(_skill_md(name), anchor)
        for var in env_vars:
            assert var in section, f"{name}: 下载步骤必须引用 {var}"


def test_delivery_contract_proxy_url_markdown_link_same_origin():
    """Reported job links are same-origin proxy markdown links; MGW console URL is not an API base.

    `$MEDIA_GATEWAY_CONSOLE_URL`（settings.display_console_url()）指向 MGW 控制台
    外部地址，MGW 不提供 `/api/media-gateway/file` 代理路由（实测 404）——产物
    链接必须写相对代理路径，由 deer-flow 同源会话 cookie 鉴权播放。
    """
    for name, (anchor, _) in MEDIA_SKILLS.items():
        section = _delivery_section(_skill_md(name), anchor)
        assert "/api/media-gateway/file/" in section, f"{name}: 必须包含 deer-flow 代理路径"
        assert "Markdown 链接" in section, f"{name}: 必须要求 Markdown 链接形式"
        assert "代码块" in section, f"{name}: 必须禁止把链接放代码块里"
        assert "不要**用 `$MEDIA_GATEWAY_CONSOLE_URL`" in section, (
            f"{name}: 必须明确警告不得用 $MEDIA_GATEWAY_CONSOLE_URL 拼产物链接"
        )


def test_delivery_contract_requires_job_id_and_failure_report():
    """job_id must be reported per job; failures surfaced verbatim, never as 完成."""
    for name, (anchor, _) in MEDIA_SKILLS.items():
        section = _delivery_section(_skill_md(name), anchor)
        assert "`job_id`" in section, f"{name}: 必须强制汇报 job_id"
        assert "状态 + 错误" in section and "不得只写" in section, f"{name}: 必须强制暴露失败作业详情"


def test_proxy_url_shape_is_relative_proxy_markdown_link():
    """The documented link template is the relative proxy path in a markdown link."""
    for name, (anchor, _) in MEDIA_SKILLS.items():
        section = _delivery_section(_skill_md(name), anchor)
        assert re.search(r"\]\(/api/media-gateway/file/", section), (
            f"{name}: 必须给出 ](/api/media-gateway/file/…) 形式的相对链接模板"
        )
