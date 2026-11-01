import type { Run } from "@langchain/langgraph-sdk";
import { describe, expect, test } from "@rstest/core";

import { pickActiveRunId } from "@/core/threads/hooks";

// Adopting an in-flight run on page open. The SDK's `reconnectOnMount` only
// follows a sessionStorage key written by the tab that started the run, so a
// thread opened elsewhere (another tab, another device, a closed tab, or a run
// started by an IM channel) has no key and would render an idle composer over a
// stale snapshot. `runs.list` is newest-first; only the newest run decides,
// because an older active run has already been superseded.

function makeRun(
  overrides: Partial<Pick<Run, "run_id" | "status">> = {},
): Pick<Run, "run_id" | "status"> {
  return { run_id: "run-latest", status: "success", ...overrides };
}

describe("pickActiveRunId", () => {
  test("adopts the latest run while it is still running", () => {
    expect(pickActiveRunId([makeRun({ status: "running" })])).toBe("run-latest");
  });

  test("adopts a queued run that has not started yet", () => {
    expect(pickActiveRunId([makeRun({ status: "pending" })])).toBe("run-latest");
  });

  test.each([
    "success",
    "error",
    "timeout",
    "interrupted",
  ] as const)("does not adopt a run that ended in %s", (status) => {
    expect(pickActiveRunId([makeRun({ status })])).toBeUndefined();
  });

  test("ignores an older active run once a newer one exists", () => {
    expect(
      pickActiveRunId([
        makeRun({ run_id: "run-newer", status: "success" }),
        makeRun({ run_id: "run-older", status: "running" }),
      ]),
    ).toBeUndefined();
  });

  test("adopts the newest run when it is the active one", () => {
    expect(
      pickActiveRunId([
        makeRun({ run_id: "run-newer", status: "running" }),
        makeRun({ run_id: "run-older", status: "success" }),
      ]),
    ).toBe("run-newer");
  });

  test.each([
    ["an empty list", [] as Pick<Run, "run_id" | "status">[]],
    ["an undefined list", undefined],
  ])("returns undefined for %s", (_label, runs) => {
    expect(pickActiveRunId(runs)).toBeUndefined();
  });
});
