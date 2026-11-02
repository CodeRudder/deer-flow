import { afterEach, describe, expect, test } from "@rstest/core";

import {
  cancelInterjection,
  createInterjection,
  InterjectionNoActiveRunError,
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
