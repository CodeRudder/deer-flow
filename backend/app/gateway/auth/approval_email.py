"""Small asynchronous SMTP sender for registration approval notices."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any


def _approval_config(config: Any):
    """Accept either ApprovalEmailConfig or the encompassing AppConfig."""
    candidate = config
    if hasattr(candidate, "auth"):
        candidate = candidate.auth
    if hasattr(candidate, "local_registration"):
        candidate = candidate.local_registration
    if hasattr(candidate, "approval_email"):
        candidate = candidate.approval_email
    return candidate


def _send_sync(config: Any, recipient: str) -> None:
    """Perform the blocking implicit-TLS SMTP exchange in a worker thread."""
    settings = _approval_config(config)
    smtp_config = getattr(settings, "smtp", None)
    from_address = getattr(settings, "from_address", None)
    if not getattr(settings, "enabled", False) or smtp_config is None or not from_address:
        raise ValueError("approval email delivery is not configured")

    login_url = getattr(settings, "login_url", None) or "/login"
    from_name = getattr(settings, "from_name", "DeerFlow")
    message = EmailMessage()
    message["Subject"] = "DeerFlow 注册申请已通过"
    message["From"] = formataddr((from_name, from_address))
    message["To"] = recipient
    message.set_content(f"你的 DeerFlow 注册申请已通过管理员审批。\n\n登录地址：{login_url}（临时域名）\n请使用注册时设置的邮箱和密码登录。")

    context = ssl.create_default_context()
    server = smtplib.SMTP_SSL(
        smtp_config.host,
        smtp_config.port,
        timeout=smtp_config.timeout_seconds,
        context=context,
    )
    try:
        server.ehlo()
        server.login(smtp_config.username, smtp_config.password)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except (OSError, smtplib.SMTPException):
            # Preserve the original delivery exception, if any.
            pass


async def send_approval_email(config: Any, recipient: str) -> None:
    """Send one approval notice asynchronously.

    ``config`` may be ``ApprovalEmailConfig`` or ``AppConfig``.  Delivery
    errors are intentionally propagated so the caller can mark the row as
    ``failed`` and offer a retry; no SMTP details are returned to clients.
    """
    await asyncio.to_thread(_send_sync, config, recipient)


__all__ = ["send_approval_email"]
