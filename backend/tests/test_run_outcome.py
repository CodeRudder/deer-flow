from langchain_core.messages import AIMessage

from deerflow.runtime.run_outcome import RunOutcomeTracker


def _fallback(*, detail: str | None = None, reason: str | None = None, content: str = "Fallback") -> AIMessage:
    additional_kwargs: dict[str, object] = {"deerflow_error_fallback": True}
    if detail is not None:
        additional_kwargs["error_detail"] = detail
    if reason is not None:
        additional_kwargs["error_reason"] = reason
    return AIMessage(content=content, additional_kwargs=additional_kwargs)


def test_run_outcome_starts_without_fallback() -> None:
    outcome = RunOutcomeTracker(run_id="run-1")

    assert outcome.run_id == "run-1"
    assert outcome.had_llm_error_fallback is False
    assert outcome.llm_error_fallback_message is None


def test_run_outcome_records_error_detail() -> None:
    outcome = RunOutcomeTracker(run_id="run-1")

    outcome.record_llm_error_fallback(_fallback(detail="Connection error.", reason="transient"))

    assert outcome.had_llm_error_fallback is True
    assert outcome.llm_error_fallback_message == "Connection error."


def test_run_outcome_keeps_first_fallback() -> None:
    outcome = RunOutcomeTracker(run_id="run-1")

    outcome.record_llm_error_fallback(_fallback(detail="First error."))
    outcome.record_llm_error_fallback(_fallback(detail="Second error."))

    assert outcome.llm_error_fallback_message == "First error."


def test_run_outcome_ignores_unmarked_ai_message() -> None:
    outcome = RunOutcomeTracker(run_id="run-1")

    outcome.record_llm_error_fallback(AIMessage(content="Normal response"))

    assert outcome.had_llm_error_fallback is False
