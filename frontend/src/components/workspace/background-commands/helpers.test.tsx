import { describe, expect, it } from "vitest";

import { render, screen } from "@testing-library/react";

import { RelativeTime, StatusIcon, statusBadgeClass } from "./helpers";
import type { CommandStatus } from "./types";

// ---------------------------------------------------------------------------
// statusBadgeClass
// ---------------------------------------------------------------------------

describe("statusBadgeClass", () => {
  it("returns blue classes for running", () => {
    expect(statusBadgeClass("running")).toContain("bg-blue-100");
  });

  it("returns green classes for completed", () => {
    expect(statusBadgeClass("completed")).toContain("bg-green-100");
  });

  it("returns red classes for failed", () => {
    expect(statusBadgeClass("failed")).toContain("bg-red-100");
  });

  it("returns red classes for timed_out", () => {
    expect(statusBadgeClass("timed_out")).toContain("bg-red-100");
  });

  it("returns gray classes for killed", () => {
    expect(statusBadgeClass("killed")).toContain("bg-gray-100");
  });
});

// ---------------------------------------------------------------------------
// StatusIcon
// ---------------------------------------------------------------------------

describe("StatusIcon", () => {
  it.each(["running", "completed", "failed", "timed_out", "killed"] satisfies CommandStatus[])(
    "renders without crashing for status=%s",
    (status) => {
      const { container } = render(<StatusIcon status={status} />);
      // lucide icons render SVGs with aria-hidden, so use container query
      expect(container.querySelector("svg")).toBeTruthy();
    },
  );
});

// ---------------------------------------------------------------------------
// RelativeTime
// ---------------------------------------------------------------------------

describe("RelativeTime", () => {
  it("renders seconds ago for recent dates", () => {
    const now = new Date();
    render(<RelativeTime date={now.toISOString()} />);
    // Should show "0s ago" or similar
    expect(screen.getByText(/s ago/)).toBeTruthy();
  });
});
