import { describe, expect, test } from "@rstest/core";

import {
  groupThreads,
  threadTimeGroupId,
} from "@/components/workspace/mobile/thread-groups";

/**
 * The grouper is the only part of the mobile list with branching worth a unit
 * test: it is pure, and its boundaries are calendar-day based, which is exactly
 * where an off-by-one-day bug hides. `now` is injected so the tests never
 * depend on when they run.
 */

// A fixed local "now": 2026-09-12 10:00 local time.
const NOW = new Date(2026, 8, 12, 10, 0, 0);

function localIso(
  year: number,
  monthIndex: number,
  day: number,
  hour = 12,
  minute = 0,
) {
  return new Date(year, monthIndex, day, hour, minute).toISOString();
}

describe("threadTimeGroupId", () => {
  test("buckets a timestamp earlier today as today", () => {
    // 00:01 today is still today despite being ~10 hours ago.
    expect(threadTimeGroupId(localIso(2026, 8, 12, 0, 1), NOW)).toBe("today");
  });

  test("buckets yesterday's last minute as yesterday, not today", () => {
    // 23:59 yesterday is 10 hours ago but reads as "yesterday" on a day header.
    expect(threadTimeGroupId(localIso(2026, 8, 11, 23, 59), NOW)).toBe(
      "yesterday",
    );
  });

  test("buckets the day before yesterday as earlier", () => {
    expect(threadTimeGroupId(localIso(2026, 8, 10, 12, 0), NOW)).toBe(
      "earlier",
    );
  });

  test("treats a future timestamp as today rather than hiding the row", () => {
    // Client/gateway clock skew must not push a fresh thread out of the list.
    expect(threadTimeGroupId(localIso(2026, 8, 13, 9, 0), NOW)).toBe("today");
  });

  test("falls back to earlier for missing or unparseable timestamps", () => {
    expect(threadTimeGroupId(null, NOW)).toBe("earlier");
    expect(threadTimeGroupId(undefined, NOW)).toBe("earlier");
    expect(threadTimeGroupId("not-a-date", NOW)).toBe("earlier");
  });
});

describe("groupThreads", () => {
  type Row = { id: string; updatedAt: string; pinned?: boolean };

  const thread = (id: string, updatedAt: string, pinned = false): Row => ({
    id,
    updatedAt,
    pinned,
  });

  const group = (rows: Row[], now: Date = NOW) =>
    groupThreads(rows, {
      getUpdatedAt: (row) => row.updatedAt,
      isPinned: (row) => row.pinned === true,
      now,
    });

  test("emits groups in prototype order and preserves order inside each", () => {
    const groups = group([
      thread("old-1", localIso(2026, 8, 1)),
      thread("today-1", localIso(2026, 8, 12, 9)),
      thread("yesterday-1", localIso(2026, 8, 11, 9)),
      thread("today-2", localIso(2026, 8, 12, 8)),
    ]);

    expect(groups.map((g) => g.id)).toEqual(["today", "yesterday", "earlier"]);
    expect(groups[0]!.threads.map((t) => t.id)).toEqual(["today-1", "today-2"]);
    expect(groups[1]!.threads.map((t) => t.id)).toEqual(["yesterday-1"]);
    expect(groups[2]!.threads.map((t) => t.id)).toEqual(["old-1"]);
  });

  test("pinned threads lead the list regardless of their age", () => {
    // The whole point of a pin: an old thread must still be reachable at the
    // top. Hoisting before grouping would not survive the time buckets.
    const groups = group([
      thread("today-1", localIso(2026, 8, 12, 9)),
      thread("old-1", localIso(2026, 8, 1), true),
      thread("yesterday-1", localIso(2026, 8, 11, 9)),
    ]);

    expect(groups[0]!.id).toBe("pinned");
    expect(groups[0]!.threads.map((t) => t.id)).toEqual(["old-1"]);
    // The pinned thread is removed from its time bucket, not duplicated into it.
    expect(groups.map((g) => g.id)).toEqual(["pinned", "today", "yesterday"]);
  });

  test("drops the pinned group entirely when nothing is pinned", () => {
    const groups = group([thread("today-1", localIso(2026, 8, 12, 9))]);

    expect(groups.map((g) => g.id)).toEqual(["today"]);
  });

  test("drops empty groups instead of rendering a bare header", () => {
    const groups = group([thread("today-1", localIso(2026, 8, 12, 9))]);

    expect(groups).toHaveLength(1);
    expect(groups[0]!.id).toBe("today");
  });

  test("returns nothing for an empty list", () => {
    expect(group([])).toEqual([]);
  });
});
