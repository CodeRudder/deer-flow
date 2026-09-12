"""Unit tests for `apply_todo_ops`'s status-transition timestamps.

The phone shows how long each todo took (prototype ⑨), and neither LangChain's
`Todo` TypedDict nor the transcript carries a clock — so `apply_todo_ops`, the
one place the `write_todos` tool funnels incremental writes through, stamps the
two marks itself.

The whole feature rests on the stamps being written on the *transition* and not
on the write: the helper agent calls `write_todos` on every turn it touches the
list, so a mark re-stamped on every call would always read "just now" and every
duration on the phone would be 0s. Every test here therefore asserts against an
injected clock that is wound forward between calls, so "the stamp did not move"
is a value comparison rather than a claim.
"""

import pytest

from deerflow.agents.thread_state import apply_todo_ops

# Two distinguishable instants; the second one is what a re-stamp would write.
FIRST = "2026-09-13T06:00:00+00:00"
SECOND = "2026-09-13T06:12:30+00:00"


class _Clock:
    """A hand-wound wall clock: `value` is what the next stamp reads."""

    def __init__(self, value: str = FIRST) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _apply(existing, updates=None, adds=None, clock=None):
    return apply_todo_ops(existing, updates, adds, now=clock or _Clock())


class TestStartedAt:
    """`pending -> in_progress` is when a todo starts."""

    def test_stamps_started_at_on_the_transition(self, clock):
        result = _apply(
            [{"content": "Draft", "status": "pending"}],
            [{"index": 0, "status": "in_progress"}],
            clock=clock,
        )

        assert result[0]["started_at"] == FIRST
        assert "completed_at" not in result[0]

    def test_does_not_refresh_on_a_rewrite_of_the_same_status(self, clock):
        """THE test for this feature: `write_todos` repeats itself."""
        existing = [{"content": "Draft", "status": "in_progress", "started_at": FIRST}]
        clock.value = SECOND

        result = _apply(existing, [{"index": 0, "status": "in_progress"}], clock=clock)

        assert result[0]["started_at"] == FIRST

    def test_a_todo_with_no_status_counts_as_a_transition(self, clock):
        # The helper agent may publish items without a status; nothing has
        # started on them yet, so entering `in_progress` is a real change.
        result = _apply(
            [{"content": "Draft"}],
            [{"index": 0, "status": "in_progress"}],
            clock=clock,
        )

        assert result[0]["started_at"] == FIRST


class TestCompletedAt:
    """`in_progress -> completed` is when a todo stops."""

    def test_stamps_completed_at_and_keeps_started_at(self, clock):
        existing = [{"content": "Draft", "status": "in_progress", "started_at": FIRST}]
        clock.value = SECOND

        result = _apply(existing, [{"index": 0, "status": "completed"}], clock=clock)

        # Both ends are needed: the phone shows their difference.
        assert result[0]["started_at"] == FIRST
        assert result[0]["completed_at"] == SECOND

    def test_does_not_refresh_on_a_rewrite_of_the_same_status(self, clock):
        existing = [
            {
                "content": "Draft",
                "status": "completed",
                "started_at": FIRST,
                "completed_at": SECOND,
            }
        ]
        clock.value = "2026-09-13T07:00:00+00:00"

        result = _apply(existing, [{"index": 0, "status": "completed"}], clock=clock)

        assert result[0]["started_at"] == FIRST
        assert result[0]["completed_at"] == SECOND

    def test_a_jump_straight_to_completed_stamps_only_the_end(self, clock):
        # No `started_at` is invented: an item that was never seen running has
        # no observed start, and the phone draws no duration rather than a lie.
        result = _apply(
            [{"content": "Draft", "status": "pending"}],
            [{"index": 0, "status": "completed"}],
            clock=clock,
        )

        assert result[0]["completed_at"] == FIRST
        assert "started_at" not in result[0]


class TestBackwardsTransitions:
    """Statuses can move backwards; the marks follow the current run."""

    def test_rework_restarts_the_clock_and_drops_the_old_end(self, clock):
        existing = [
            {
                "content": "Draft",
                "status": "completed",
                "started_at": FIRST,
                "completed_at": SECOND,
            }
        ]
        clock.value = "2026-09-13T07:00:00+00:00"

        result = _apply(existing, [{"index": 0, "status": "in_progress"}], clock=clock)

        # The duration that matters is the run that is happening now.
        assert result[0]["started_at"] == "2026-09-13T07:00:00+00:00"
        assert "completed_at" not in result[0]

    def test_a_todo_sent_back_to_pending_keeps_no_marks(self, clock):
        existing = [
            {
                "content": "Draft",
                "status": "in_progress",
                "started_at": FIRST,
            }
        ]
        clock.value = SECOND

        result = _apply(existing, [{"index": 0, "status": "pending"}], clock=clock)

        assert "started_at" not in result[0]
        assert "completed_at" not in result[0]


class TestUntouchedWrites:
    """Anything that is not a status change leaves the marks alone."""

    def test_a_content_only_update_stamps_nothing(self, clock):
        existing = [{"content": "Draft", "status": "in_progress", "started_at": FIRST}]
        clock.value = SECOND

        result = _apply(existing, [{"index": 0, "content": "Draft it"}], clock=clock)

        assert result[0] == {
            "content": "Draft it",
            "status": "in_progress",
            "started_at": FIRST,
        }

    def test_marks_on_other_items_survive(self, clock):
        existing = [
            {"content": "Draft", "status": "completed", "started_at": FIRST, "completed_at": SECOND},
            {"content": "Review", "status": "pending"},
        ]
        result = _apply(existing, [{"index": 1, "status": "in_progress"}], clock=clock)

        assert result[0]["started_at"] == FIRST
        assert result[0]["completed_at"] == SECOND
        assert result[1]["started_at"] == FIRST

    def test_an_out_of_range_update_changes_nothing(self, clock):
        existing = [{"content": "Draft", "status": "pending"}]
        clock.value = SECOND

        result = _apply(existing, [{"index": 9, "status": "in_progress"}], clock=clock)

        assert result == [{"content": "Draft", "status": "pending"}]

    def test_a_removed_item_takes_its_marks_with_it(self, clock):
        existing = [
            {"content": "Draft", "status": "in_progress", "started_at": FIRST},
            {"content": "Review", "status": "pending"},
        ]
        result = _apply(existing, [{"index": 0, "remove": True}], clock=clock)

        assert result == [{"content": "Review", "status": "pending"}]


class TestAdds:
    """A new item has no history, so only a running one is stamped."""

    def test_a_new_pending_todo_carries_no_marks(self, clock):
        result = _apply(None, adds=[{"content": "Draft"}], clock=clock)

        assert result == [{"content": "Draft", "status": "pending"}]

    def test_a_new_in_progress_todo_is_stamped(self, clock):
        # `adds` accepts a status, so an item can be born running. It entered
        # that status on this very call — the stamp is not a re-write.
        result = _apply(
            None,
            adds=[{"content": "Draft", "status": "in_progress"}],
            clock=clock,
        )

        assert result[0]["started_at"] == FIRST
