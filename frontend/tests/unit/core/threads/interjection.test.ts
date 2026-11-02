import { afterEach, describe, expect, test } from "@rstest/core";

import {
  applyAutoRunEvent,
  cancelInterjection,
  createInterjection,
  hasUnclaimedQueuedInterjections,
  InterjectionNoActiveRunError,
  type PendingInterjection,
  pruneLandedInterjections,
} from "@/core/threads/interjection";

// Thread interjection client: 202-style queued payload, 409 no-active-run
// classification, and cancel result mapping (204/409/404).

const fetchMock = async (impl: (input: RequestInfo) => Promise<Response>) => {
  const original = globalThis.fetch;
  globalThis.fetch = impl as typeof fetch;
  return () => {
    globalThis.fetch = original;
  };
};

afterEach(() => {
  // safety net in case a test forgets to restore
});

describe("createInterjection", () => {
  test("returns the queued interjection payload on 200", async () => {
    const restore = await fetchMock(async () => {
      return new Response(
        JSON.stringify({
          id: "int-1",
          thread_id: "t1",
          run_id: "r1",
          text: "hello",
          source: "web",
          seq: 1,
          created_at: "2026-11-01T00:00:00Z",
          applied: false,
        }),
        { status: 200 },
      );
    });
    try {
      const item = await createInterjection("t1", "hello");
      expect(item.id).toBe("int-1");
      expect(item.applied).toBe(false);
    } finally {
      restore();
    }
  });

  test("throws InterjectionNoActiveRunError on 409", async () => {
    const restore = await fetchMock(async () => {
      return new Response(
        JSON.stringify({ detail: "Thread t1 has no in-flight run" }),
        { status: 409 },
      );
    });
    try {
      await expect(createInterjection("t1", "hello")).rejects.toBeInstanceOf(
        InterjectionNoActiveRunError,
      );
    } finally {
      restore();
    }
  });

  test("surfaces the gateway detail on 429", async () => {
    const restore = await fetchMock(async () => {
      return new Response(JSON.stringify({ detail: "queue full (max 8)" }), {
        status: 429,
      });
    });
    try {
      let message = "";
      try {
        await createInterjection("t1", "hello");
      } catch (error) {
        message = error instanceof Error ? error.message : "";
      }
      expect(message).toBe("queue full (max 8)");
    } finally {
      restore();
    }
  });
});

describe("cancelInterjection", () => {
  test("maps 204 to cancelled", async () => {
    const restore = await fetchMock(
      async () => new Response(null, { status: 204 }),
    );
    try {
      expect(await cancelInterjection("t1", "int-1")).toBe("cancelled");
    } finally {
      restore();
    }
  });

  test("maps 409 to already_applied", async () => {
    const restore = await fetchMock(
      async () => new Response(null, { status: 409 }),
    );
    try {
      expect(await cancelInterjection("t1", "int-1")).toBe("already_applied");
    } finally {
      restore();
    }
  });

  test("maps 404 to not_found", async () => {
    const restore = await fetchMock(
      async () => new Response(null, { status: 404 }),
    );
    try {
      expect(await cancelInterjection("t1", "int-1")).toBe("not_found");
    } finally {
      restore();
    }
  });
});

describe("interjection run-end auto-run state transitions", () => {
  const base = {
    id: "int-1",
    thread_id: "t1",
    run_id: "r1",
    text: "first ask",
    source: "web",
    seq: 1,
    created_at: "2026-11-02T00:00:00Z",
    applied: false,
  };

  test("applyAutoRunEvent marks listed bubbles and leaves others", () => {
    const items: PendingInterjection[] = [
      { ...base },
      { ...base, id: "int-2", text: "second ask", seq: 2 },
    ];
    const updated = applyAutoRunEvent(items, {
      new_run_id: "r-new",
      input: "first ask\n\nsecond ask",
      interjections: [{ id: "int-1" }, { id: "int-2" }],
    });
    const first = updated[0]!;
    const second = updated[1]!;
    expect(first.auto_run_run_id).toBe("r-new");
    expect(first.auto_run_input).toBe("first ask\n\nsecond ask");
    expect(second.auto_run_run_id).toBe("r-new");
    // Source list untouched.
    expect(items[0]!.auto_run_run_id).toBeUndefined();
  });

  test("applyAutoRunEvent with no listed ids is a no-op", () => {
    const items: PendingInterjection[] = [{ ...base }];
    expect(applyAutoRunEvent(items, {})).toBe(items);
    expect(applyAutoRunEvent(items, { interjections: [] })).toBe(items);
  });

  test("pruneLandedInterjections drops applied and auto-run bubbles once their text lands", () => {
    const items: PendingInterjection[] = [
      { ...base, applied: true },
      { ...base, id: "int-2", text: "second", seq: 2, auto_run_run_id: "r-new", auto_run_input: "first\n\nsecond" },
      { ...base, id: "int-3", text: "third", seq: 3 },
    ];
    // Merged human message landed in the transcript.
    const remaining = pruneLandedInterjections(items, new Set(["first\n\nsecond"]));
    // Applied bubble with unmatched text stays; auto-run bubble pruned; plain queued stays.
    expect(remaining.map((i) => i.id)).toEqual(["int-1", "int-3"]);
  });

  test("pruneLandedInterjections keeps auto-run bubbles without a landed merged input", () => {
    const items: PendingInterjection[] = [
      { ...base, auto_run_run_id: "r-new", auto_run_input: "first\n\nsecond" },
    ];
    expect(pruneLandedInterjections(items, new Set(["other"]))).toHaveLength(1);
  });

  test("hasUnclaimedQueuedInterjections drives the drop-toast fallback", () => {
    expect(hasUnclaimedQueuedInterjections([{ ...base }])).toBe(true);
    expect(hasUnclaimedQueuedInterjections([{ ...base, applied: true }])).toBe(false);
    expect(
      hasUnclaimedQueuedInterjections([
        { ...base, auto_run_run_id: "r-new", auto_run_input: "x" },
      ]),
    ).toBe(false);
  });
});
