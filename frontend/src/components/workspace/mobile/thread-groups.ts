/**
 * Grouping for the mobile thread list (prototype ①: 今天 / 昨天 / 更早).
 *
 * No grouper existed to reuse — the desktop sidebar (`recent-chat-list.tsx`)
 * and the desktop list page (`app/workspace/chats/page.tsx`) both render one
 * flat list — so this is new. It lives outside `core/**` on purpose: T4's
 * contract forbids touching `core/`, and this is mobile presentation, not
 * thread domain logic.
 *
 * Kept pure (caller passes `now`) so the boundaries are testable without
 * freezing the clock, and so the "the date rolled over at midnight" case is
 * expressible in a test rather than being a flake.
 */

export type ThreadGroupId = "pinned" | "today" | "yesterday" | "earlier";

export type ThreadTimeGroupId = Exclude<ThreadGroupId, "pinned">;

export type ThreadGroup<T> = {
  id: ThreadGroupId;
  threads: T[];
};

/**
 * Calendar-day bucket, not a rolling 24h window: 23:50 yesterday is "yesterday"
 * to a reader even though it is 20 minutes ago, and the prototype's group
 * headers are day labels.
 *
 * An unparseable or missing timestamp lands in "earlier" rather than throwing —
 * a thread with a broken `updated_at` must not blank the whole list.
 */
export function threadTimeGroupId(
  updatedAt: string | null | undefined,
  now: Date,
): ThreadTimeGroupId {
  if (!updatedAt) {
    return "earlier";
  }
  const parsed = new Date(updatedAt);
  if (Number.isNaN(parsed.getTime())) {
    return "earlier";
  }

  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  ).getTime();
  const timestamp = parsed.getTime();

  if (timestamp >= startOfToday) {
    return "today";
  }
  // "Yesterday" is the whole calendar day before today, so the window is
  // [startOfToday - 24h, startOfToday). A timestamp in the future is "today"
  // (handled above) — clock skew between client and gateway must not hide rows.
  const startOfYesterday = startOfToday - 24 * 60 * 60 * 1000;
  if (timestamp >= startOfYesterday) {
    return "yesterday";
  }
  return "earlier";
}

/**
 * Bucket `threads` for the list, preserving input order inside every group and
 * emitting groups in prototype order. Empty groups are dropped so the list
 * never renders a bare header.
 *
 * Pinned threads get their own leading group rather than being hoisted *before*
 * grouping: grouping is what decides row order, so a pin applied to the
 * pre-grouping list is immediately undone by the buckets — pinning a thread
 * from "Earlier" would leave it exactly where it was. A leading group is also
 * what the user expects: the pin is a deliberate "keep this at the top", and
 * `pinned` taking the first slot makes that true at every scroll position.
 */
export function groupThreads<T>(
  threads: T[],
  {
    getUpdatedAt,
    isPinned,
    now,
  }: {
    getUpdatedAt: (thread: T) => string | null | undefined;
    isPinned: (thread: T) => boolean;
    now: Date;
  },
): ThreadGroup<T>[] {
  const pinned: T[] = [];
  const buckets: Record<ThreadTimeGroupId, T[]> = {
    today: [],
    yesterday: [],
    earlier: [],
  };

  for (const thread of threads) {
    if (isPinned(thread)) {
      pinned.push(thread);
    } else {
      buckets[threadTimeGroupId(getUpdatedAt(thread), now)].push(thread);
    }
  }

  const order: ThreadGroupId[] = ["pinned", "today", "yesterday", "earlier"];
  return order
    .map((id) => ({
      id,
      threads: id === "pinned" ? pinned : buckets[id],
    }))
    .filter((group) => group.threads.length > 0);
}
