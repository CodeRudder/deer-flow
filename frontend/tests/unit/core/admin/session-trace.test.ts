import { expect, test } from "@rstest/core";

import {
  getTraceMode,
  shouldLoadTraceOverview,
} from "@/core/admin/session-trace";

test("only a selected user enables overview mode", () => {
  const mode = getTraceMode("user-1", "", "");
  expect(mode).toBe("user");
  expect(shouldLoadTraceOverview(mode)).toBe(true);
});

test("thread or run id always switches to exact mode", () => {
  expect(getTraceMode("user-1", "thread-1", "")).toBe("exact");
  expect(getTraceMode("user-1", "", "run-1")).toBe("exact");
  expect(getTraceMode(null, "thread-1", "run-1")).toBe("exact");
  expect(shouldLoadTraceOverview("exact")).toBe(false);
});

test("empty filters use all-runs mode without overview", () => {
  expect(getTraceMode(null, "", "")).toBe("all");
  expect(shouldLoadTraceOverview("all")).toBe(false);
});
