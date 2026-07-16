import { describe, expect, it } from "@rstest/core";

import {
  buildDonutSegments,
  buildTrendCoordinates,
  placeFloatingTooltip,
  prioritizeQuotaModelGroups,
  quotaProgressTone,
  smoothTrendPath,
} from "@/components/workspace/admin/admin-dashboard-helpers";

describe("buildDonutSegments", () => {
  it("normalizes values and accumulates segment offsets", () => {
    expect(buildDonutSegments([60, 30, 10])).toEqual([
      { share: 60, offset: 0 },
      { share: 30, offset: 60 },
      { share: 10, offset: 90 },
    ]);
  });

  it("returns empty segments for an all-zero distribution", () => {
    expect(buildDonutSegments([0, 0])).toEqual([
      { share: 0, offset: 0 },
      { share: 0, offset: 0 },
    ]);
  });
});

describe("quotaProgressTone", () => {
  it("uses neutral styling when quota checking is disabled", () => {
    expect(quotaProgressTone(false, 900, 1000)).toBe("disabled");
  });

  it("uses normal, warning, and exceeded tones at their thresholds", () => {
    expect(quotaProgressTone(true, 799, 1000)).toBe("normal");
    expect(quotaProgressTone(true, 800, 1000)).toBe("warning");
    expect(quotaProgressTone(true, 1000, 1000)).toBe("exceeded");
  });
});

describe("prioritizeQuotaModelGroups", () => {
  it("puts exceeded, warning, and temporary overrides first", () => {
    const groups = [
      { scope_id: "unlimited", status: "unlimited", source: "scope_default" },
      { scope_id: "override", status: "normal", source: "temporary_override" },
      { scope_id: "warning", status: "warning", source: "scope_default" },
      { scope_id: "exceeded", status: "exceeded", source: "scope_default" },
      { scope_id: "normal", status: "normal", source: "scope_default" },
    ] as const;

    expect(
      prioritizeQuotaModelGroups(groups).map((group) => group.scope_id),
    ).toEqual(["exceeded", "warning", "override", "normal", "unlimited"]);
  });

  it("does not mutate groups with the same priority", () => {
    const groups = [
      { scope_id: "first", status: "normal", source: "scope_default" },
      { scope_id: "second", status: "normal", source: "scope_default" },
    ] as const;

    expect(
      prioritizeQuotaModelGroups(groups).map((group) => group.scope_id),
    ).toEqual(["first", "second"]);
    expect(groups.map((group) => group.scope_id)).toEqual(["first", "second"]);
  });
});

describe("placeFloatingTooltip", () => {
  it("places the tooltip to the right of the pointer when space is available", () => {
    expect(placeFloatingTooltip(1000, 500, 200, 250, 260, 108)).toEqual({
      left: 214,
      top: 196,
    });
  });

  it("flips to the left and stays inside the container near an edge", () => {
    expect(placeFloatingTooltip(1000, 500, 900, 20, 260, 108)).toEqual({
      left: 626,
      top: 8,
    });
  });
});

describe("buildTrendCoordinates", () => {
  it("centers a single point and keeps it inside the plot", () => {
    expect(
      buildTrendCoordinates(
        [{ label: "10:00", tokens: 50, requests: 2, images: 1 }],
        "tokens",
      ),
    ).toEqual([{ x: 388, y: 24 }]);
  });

  it("spreads points across the plot and scales each metric independently", () => {
    const points = [
      { label: "Mon", tokens: 0, requests: 10, images: 2 },
      { label: "Tue", tokens: 100, requests: 5, images: 4 },
      { label: "Wed", tokens: 50, requests: 0, images: 1 },
    ];

    expect(buildTrendCoordinates(points, "tokens")).toEqual([
      { x: 48, y: 216 },
      { x: 388, y: 24 },
      { x: 728, y: 120 },
    ]);
    expect(buildTrendCoordinates(points, "requests")[0]).toEqual({
      x: 48,
      y: 24,
    });
  });
});

describe("smoothTrendPath", () => {
  it("creates a curved path through every coordinate", () => {
    expect(
      smoothTrendPath([
        { x: 48, y: 216 },
        { x: 388, y: 24 },
        { x: 728, y: 120 },
      ]),
    ).toBe("M 48 216 C 218 216, 218 24, 388 24 C 558 24, 558 120, 728 120");
  });
});
