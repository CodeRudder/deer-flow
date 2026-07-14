"""Run-scoped terminal outcome signals shared by the worker and middleware."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import AIMessage

from deerflow.utils.messages import message_to_text

RUN_OUTCOME_CONTEXT_KEY = "__run_outcome"
_DEFAULT_LLM_ERROR = "LLM provider failed after retries"


@dataclass(slots=True)
class RunOutcomeTracker:
    """Collect terminal signals produced by the current top-level run only."""

    run_id: str
    llm_error_fallback_message: str | None = None

    @property
    def had_llm_error_fallback(self) -> bool:
        return self.llm_error_fallback_message is not None

    def record_llm_error_fallback(self, message: AIMessage) -> None:
        """Record the first marked fallback produced during this run."""
        if self.llm_error_fallback_message is not None:
            return

        metadata = message.additional_kwargs
        if not metadata.get("deerflow_error_fallback"):
            return

        detail = metadata.get("error_detail")
        if isinstance(detail, str) and detail.strip():
            self.llm_error_fallback_message = detail.strip()
            return

        reason = metadata.get("error_reason")
        if isinstance(reason, str) and reason.strip():
            self.llm_error_fallback_message = reason.strip()
            return

        fallback_text = message_to_text(message, text_attribute_fallback=True).strip()
        self.llm_error_fallback_message = fallback_text[:2000] if fallback_text else _DEFAULT_LLM_ERROR
