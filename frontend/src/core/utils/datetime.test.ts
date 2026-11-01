import { describe, expect, it } from "vitest";

import { formatTimeAgo } from "@/core/utils/datetime";

describe("formatTimeAgo", () => {
  it("returns a fallback for invalid timestamps", () => {
    expect(formatTimeAgo("not-a-date")).toBe("-");
  });

  it("formats valid timestamps", () => {
    const result = formatTimeAgo("2026-01-01T00:00:00Z", "en-US");
    expect(result).toContain("ago");
  });
});
