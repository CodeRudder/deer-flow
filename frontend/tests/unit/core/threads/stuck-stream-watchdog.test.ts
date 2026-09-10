import type { Run } from "@langchain/langgraph-sdk";
import { describe, expect, test } from "@rstest/core";


import { shouldAbortStalledStream } from "@/core/threads/hooks";

// Stuck-stream watchdog: when the SSE connection dies mid-run the SDK keeps
// `isLoading` true forever and the composer stays on the stop button. The
// watchdog aborts only when the backend run is already terminal, the run
// belongs to the current stream, and the message list has been silent for
// the grace period.

const CYCLE_START = 1_000_000;
const LAST_PROGRESS = CYCLE_START + 5_000;
const NOW_STALLED = LAST_PROGRESS + 30_000;
const NOW_FRESH = LAST_PROGRESS + 1_000;

function makeRun(
  overrides: Partial<Pick<Run, "run_id" | "status" | "created_at">> = {},
): Pick<Run, "run_id" | "status" | "created_at"> {
  return {
    run_id: "run-current",
    status: "success",
    // Created before the loading cycle started: matches the reconnect path.
    created_at: new Date(CYCLE_START - 60_000).toISOString(),
    ...overrides,
  };
}

describe("shouldAbortStalledStream", () => {
  test("aborts a terminal run of the current stream after the grace period", () => {
    expect(
      shouldAbortStalledStream({
        latestRun: makeRun(),
        currentStreamRunId: "run-current",
        streamCycleStartAt: CYCLE_START,
        lastProgressAt: LAST_PROGRESS,
        now: NOW_STALLED,
      }),
    ).toBe(true);
  });

  test("aborts a terminal run created before the cycle (reconnect path)", () => {
    expect(
      shouldAbortStalledStream({
        latestRun: makeRun({ run_id: "run-old" }),
        currentStreamRunId: null,
        streamCycleStartAt: CYCLE_START,
        lastProgressAt: LAST_PROGRESS,
        now: NOW_STALLED,
      }),
    ).toBe(true);
  });

  test("does not abort while the stream is still within the grace period", () => {
    expect(
      shouldAbortStalledStream({
        latestRun: makeRun(),
        currentStreamRunId: "run-current",
        streamCycleStartAt: CYCLE_START,
        lastProgressAt: LAST_PROGRESS,
        now: NOW_FRESH,
      }),
    ).toBe(false);
  });

  test("does not abort while the latest run is still running", () => {
    expect(
      shouldAbortStalledStream({
        latestRun: makeRun({ status: "running" }),
        currentStreamRunId: "run-current",
        streamCycleStartAt: CYCLE_START,
        lastProgressAt: LAST_PROGRESS,
        now: NOW_STALLED,
      }),
    ).toBe(false);
  });

  test("does not abort a pending run", () => {
    expect(
      shouldAbortStalledStream({
        latestRun: makeRun({ status: "pending" }),
        currentStreamRunId: "run-current",
        streamCycleStartAt: CYCLE_START,
        lastProgressAt: LAST_PROGRESS,
        now: NOW_STALLED,
      }),
    ).toBe(false);
  });

  test("does not abort when the latest run is from a previous turn", () => {
    // A fresh stream has not created its run yet (run id unknown) while the
    // newest list entry is a stale interrupted run created after the cycle
    // start would only happen for the current stream, but a stale run created
    // after cycle start with a different id must never abort.
    expect(
      shouldAbortStalledStream({
        latestRun: makeRun({
          run_id: "run-stale",
          status: "interrupted",
          created_at: new Date(CYCLE_START + 60_000).toISOString(),
        }),
        currentStreamRunId: null,
        streamCycleStartAt: CYCLE_START,
        lastProgressAt: LAST_PROGRESS,
        now: NOW_STALLED,
      }),
    ).toBe(false);
  });

  test("does not abort when no run data has loaded yet", () => {
    expect(
      shouldAbortStalledStream({
        latestRun: undefined,
        currentStreamRunId: "run-current",
        streamCycleStartAt: CYCLE_START,
        lastProgressAt: LAST_PROGRESS,
        now: NOW_STALLED,
      }),
    ).toBe(false);
  });

  test("aborts an interrupted run of the current stream (cancelled by user)", () => {
    expect(
      shouldAbortStalledStream({
        latestRun: makeRun({ status: "interrupted" }),
        currentStreamRunId: "run-current",
        streamCycleStartAt: CYCLE_START,
        lastProgressAt: LAST_PROGRESS,
        now: NOW_STALLED,
      }),
    ).toBe(true);
  });
});
