import { describe, expect, it } from "@rstest/core";

import {
  videoRatePerSecond,
  videoRateRangeLabel,
} from "@/core/video-generation/rate";
import type { VideoModelBilling } from "@/core/video-generation/types";

function billing(rates: Array<[string, number, number?]>): VideoModelBilling {
  return {
    resolutions: rates.map(([resolution, min, max]) => ({
      resolution,
      yuan_per_second_min: min,
      yuan_per_second_max: max ?? min,
    })),
    min_duration_seconds: 4,
    max_duration_seconds: 15,
  };
}

describe("videoRatePerSecond", () => {
  it("returns min/max across resolutions", () => {
    expect(
      videoRatePerSecond(
        billing([
          ["768P", 2],
          ["2K", 4],
        ]),
      ),
    ).toEqual({
      min: 2,
      max: 4,
    });
  });

  it("returns null without rates", () => {
    expect(videoRatePerSecond(billing([]))).toBeNull();
  });
});

describe("videoRateRangeLabel", () => {
  it("collapses equal min/max to a single value", () => {
    expect(videoRateRangeLabel(2, 2)).toBe("2");
  });

  it("joins a range with ~ and trims decimals", () => {
    expect(videoRateRangeLabel(3, 3.5)).toBe("3~3.5");
  });
});
