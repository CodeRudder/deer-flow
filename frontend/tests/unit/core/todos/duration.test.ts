import { describe, expect, test } from "@rstest/core";

import {
  formatDuration,
  formatTodoDuration,
  todoDurationMs,
} from "@/core/todos";

/**
 * The elapsed-time reading beside a todo (prototype ⑨).
 *
 * Everything here is about the two ends being *honest*: the phone draws either
 * a duration or nothing, so the interesting cases are the ones where the data
 * cannot answer — old threads have no stamps at all, and an item the agent
 * completed straight from `pending` never had a start observed.
 */

const NOW = Date.parse("2026-09-13T12:00:00.000Z");

/** An ISO instant `ms` before `NOW`. */
const ago = (ms: number) => new Date(NOW - ms).toISOString();

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

describe("formatDuration", () => {
  test("reads in seconds below a minute", () => {
    expect(formatDuration(0)).toBe("0s");
    expect(formatDuration(1_000)).toBe("1s");
    expect(formatDuration(45_000)).toBe("45s");
    expect(formatDuration(59_999)).toBe("59s");
  });

  test("reads in whole minutes below an hour", () => {
    expect(formatDuration(MINUTE)).toBe("1m");
    expect(formatDuration(12 * MINUTE)).toBe("12m");
    // Truncated, never rounded or fractional: `0.2 小时` is not a reading
    // anyone wants beside a task name.
    expect(formatDuration(12 * MINUTE + 59_000)).toBe("12m");
    expect(formatDuration(59 * MINUTE)).toBe("59m");
  });

  test("reads in hours and minutes above an hour", () => {
    expect(formatDuration(HOUR)).toBe("1h");
    expect(formatDuration(HOUR + 5 * MINUTE)).toBe("1h 5m");
    expect(formatDuration(3 * HOUR + 59 * MINUTE)).toBe("3h 59m");
  });

  test("a duration that went backwards reads as zero, not as negative", () => {
    // A clock that moved back between the two stamps; the row still has to
    // read as a duration.
    expect(formatDuration(-5 * MINUTE)).toBe("0s");
  });
});

describe("todoDurationMs", () => {
  test("a finished todo is the difference of its two ends", () => {
    const todo = {
      status: "completed" as const,
      started_at: ago(12 * MINUTE),
      completed_at: ago(0),
    };

    expect(todoDurationMs(todo, NOW)).toBe(12 * MINUTE);
  });

  test("a finished todo does not need the clock", () => {
    const todo = {
      status: "completed" as const,
      started_at: ago(12 * MINUTE),
      completed_at: ago(2 * MINUTE),
    };

    // Both ends are fixed, so the reading is the same whenever it is taken.
    expect(todoDurationMs(todo, null)).toBe(10 * MINUTE);
    expect(todoDurationMs(todo, NOW)).toBe(10 * MINUTE);
  });

  test("a running todo is measured against the clock", () => {
    const todo = {
      status: "in_progress" as const,
      started_at: ago(3 * MINUTE),
    };

    expect(todoDurationMs(todo, NOW)).toBe(3 * MINUTE);
    expect(todoDurationMs(todo, NOW + 4 * MINUTE)).toBe(7 * MINUTE);
  });

  test("a running todo before the first tick has no reading yet", () => {
    const todo = {
      status: "in_progress" as const,
      started_at: ago(3 * MINUTE),
    };

    expect(todoDurationMs(todo, null)).toBeNull();
  });

  test("a waiting todo has no duration", () => {
    expect(todoDurationMs({ status: "pending" }, NOW)).toBeNull();
    // And neither has one with no status at all, which the helper agent does
    // publish.
    expect(todoDurationMs({}, NOW)).toBeNull();
  });

  test("a stamp that is missing or unreadable yields no reading", () => {
    // THIS is the shape of every todo written before the stamps existed: the
    // reader must not crash on it and must not draw `NaN` / `Invalid Date`.
    expect(todoDurationMs({ status: "completed" }, NOW)).toBeNull();
    expect(todoDurationMs({ status: "in_progress" }, NOW)).toBeNull();
    expect(
      todoDurationMs({ status: "completed", started_at: ago(MINUTE) }, NOW),
    ).toBeNull();
    expect(
      todoDurationMs({ status: "completed", completed_at: ago(0) }, NOW),
    ).toBeNull();
    expect(
      todoDurationMs({ status: "in_progress", started_at: "not a date" }, NOW),
    ).toBeNull();
    expect(
      todoDurationMs({ status: "in_progress", started_at: "" }, NOW),
    ).toBeNull();
  });

  test("a finished todo the agent never started reads as no duration", () => {
    // The backend stamps only the end for an item completed straight from
    // `pending`; there is no observed start, so there is no duration to draw
    // — a fabricated `0s` would claim the task was instantaneous.
    const todo = {
      status: "completed" as const,
      completed_at: ago(0),
    };

    expect(todoDurationMs(todo, NOW)).toBeNull();
  });
});

describe("formatTodoDuration", () => {
  test("is the reading the row draws, or nothing at all", () => {
    expect(
      formatTodoDuration(
        {
          status: "completed",
          started_at: ago(12 * MINUTE),
          completed_at: ago(0),
        },
        NOW,
      ),
    ).toBe("12m");

    expect(formatTodoDuration({ status: "pending" }, NOW)).toBeNull();
  });
});
