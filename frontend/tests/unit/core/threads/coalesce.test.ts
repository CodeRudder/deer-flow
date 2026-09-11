import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it, rs } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";

import {
  decideCoalesce,
  STREAM_RENDER_COALESCE_MS,
  useCoalescedStreamMessages,
} from "@/core/threads/hooks";

describe("decideCoalesce", () => {
  it("flushes immediately once a full interval has elapsed (leading edge)", () => {
    expect(decideCoalesce(1000, 900, 80, false)).toEqual({
      action: "flush-now",
    });
    expect(decideCoalesce(1000, 920, 80, false)).toEqual({
      action: "flush-now",
    });
  });

  it("schedules exactly one trailing flush for the interval remainder", () => {
    expect(decideCoalesce(1000, 950, 80, false)).toEqual({
      action: "schedule",
      delayMs: 30,
    });
  });

  it("waits while a trailing flush is already pending", () => {
    expect(decideCoalesce(1000, 950, 80, true)).toEqual({ action: "wait" });
  });

  it("still takes the leading edge when a late trailing flush is pending", () => {
    // Timer callbacks queue behind long tasks, so an update can arrive past the
    // interval while the trailing flush is still armed. The decision is
    // flush-now, which obliges the call site to disarm that timer — otherwise
    // it publishes a second time and slips the next interval forward.
    expect(decideCoalesce(1000, 900, 80, true)).toEqual({
      action: "flush-now",
    });
  });

  it("never delays a flush beyond the interval, unlike a debounce", () => {
    // Simulate a dense stream: updates every 10ms. A debounce would keep
    // resetting its timer and never fire; here the trailing flush scheduled at
    // the first update stays put and every later update just waits on it.
    let lastFlush = 0;
    let pendingUntil: number | null = null;
    const flushes: number[] = [];
    for (let now = 10; now <= 400; now += 10) {
      if (pendingUntil !== null && now >= pendingUntil) {
        flushes.push(pendingUntil);
        lastFlush = pendingUntil;
        pendingUntil = null;
      }
      const decision = decideCoalesce(
        now,
        lastFlush,
        80,
        pendingUntil !== null,
      );
      if (decision.action === "flush-now") {
        flushes.push(now);
        lastFlush = now;
      } else if (decision.action === "schedule") {
        pendingUntil = now + decision.delayMs;
      }
    }
    expect(flushes.length).toBeGreaterThanOrEqual(4);
    for (let i = 1; i < flushes.length; i++) {
      const gap = flushes[i]! - flushes[i - 1]!;
      expect(gap).toBeGreaterThanOrEqual(80);
      expect(gap).toBeLessThanOrEqual(90);
    }
  });

  it("takes the leading edge when nothing has flushed yet", () => {
    // The hook seeds lastFlush with -Infinity on a monotonic clock whose epoch
    // is page load, so the first update of a stream must not be deferred.
    expect(decideCoalesce(5, Number.NEGATIVE_INFINITY, 80, false)).toEqual({
      action: "flush-now",
    });
  });

  it("keeps the scheduled delay inside the interval", () => {
    // Holds for every elapsed value a monotonic clock can produce, which is why
    // the call site needs no clamp on the timeout delay.
    for (let elapsed = 0; elapsed < 80; elapsed++) {
      const decision = decideCoalesce(1000, 1000 - elapsed, 80, false);
      expect(decision.action).toBe("schedule");
      if (decision.action !== "schedule") continue;
      expect(decision.delayMs).toBeGreaterThan(0);
      expect(decision.delayMs).toBeLessThanOrEqual(80);
    }
  });

  it("exports a frame-scale default interval", () => {
    expect(STREAM_RENDER_COALESCE_MS).toBeGreaterThanOrEqual(50);
    expect(STREAM_RENDER_COALESCE_MS).toBeLessThanOrEqual(100);
  });
});

describe("useCoalescedStreamMessages", () => {
  function message(id: string): Message {
    return { id, type: "ai", content: id } as Message;
  }

  function renderCoalescer(initial: {
    messages: Message[];
    isStreaming: boolean;
  }) {
    return renderHook(
      ({ messages, isStreaming }: { messages: Message[]; isStreaming: boolean }) =>
        useCoalescedStreamMessages(messages, isStreaming),
      { initialProps: initial },
    );
  }

  it("passes the live array through while idle", () => {
    // No snapshot exists while idle, so every idle update renders immediately
    // and the identity is the caller's own array.
    const first = [message("a")];
    const { result, rerender } = renderCoalescer({
      messages: first,
      isStreaming: false,
    });

    expect(result.current).toBe(first);

    const second = [message("a"), message("b")];
    rerender({ messages: second, isStreaming: false });

    expect(result.current).toBe(second);
  });

  it("publishes the current array on a stream's leading edge", () => {
    const first = [message("a")];
    const { result } = renderCoalescer({
      messages: first,
      isStreaming: true,
    });

    // The flush baseline starts at -Infinity, so the first streaming update
    // always takes the leading edge instead of waiting out an interval.
    expect(result.current).toBe(first);
  });

  it("drops the snapshot when the stream ends", () => {
    // A retained snapshot would be painted after a thread switch (the chat
    // page avoids re-mounting), so stream end must return the live array.
    const first = [message("a")];
    const { result, rerender } = renderCoalescer({
      messages: first,
      isStreaming: true,
    });
    expect(result.current).toBe(first);

    const settled = [message("a"), message("b")];
    rerender({ messages: settled, isStreaming: false });

    expect(result.current).toBe(settled);
  });

  it("coalesces dense updates into one publish per interval", () => {
    rs.useFakeTimers({
      toFake: ["setTimeout", "clearTimeout", "performance"],
    });
    try {
      const first = [message("a")];
      const { result, rerender } = renderCoalescer({
        messages: first,
        isStreaming: true,
      });

      // Leading edge: the first update of the stream publishes immediately.
      expect(result.current).toBe(first);

      const second = [message("a"), message("b")];
      rerender({ messages: second, isStreaming: true });
      // Inside the interval the snapshot holds until the trailing flush.
      expect(result.current).toBe(first);

      act(() => {
        rs.advanceTimersByTime(40);
      });
      const third = [message("a"), message("b"), message("c")];
      rerender({ messages: third, isStreaming: true });
      // Still inside the interval: the pending trailing flush absorbs it too.
      expect(result.current).toBe(first);

      act(() => {
        rs.advanceTimersByTime(40);
      });
      // Interval elapsed: one publish carried the latest array.
      expect(result.current).toBe(third);
    } finally {
      rs.useRealTimers();
    }
  });
});
