"""Shared display-text normalization for administrator views."""

_USER_INPUT_BEGIN = "--- BEGIN USER INPUT ---"
_USER_INPUT_END = "--- END USER INPUT ---"


def format_user_input(value: str | None, fallback: str = "") -> str:
    if not value:
        return fallback
    text = value.strip()
    if text.startswith(_USER_INPUT_BEGIN) and text.endswith(_USER_INPUT_END):
        text = text[len(_USER_INPUT_BEGIN) : -len(_USER_INPUT_END)].strip()
    return text or fallback


def format_output_preview(value: str | None) -> str:
    return " ".join((value or "").split())
