import type { VideoModelBilling } from "./types";

/** 跨分辨率的元/秒价目区间；无可用费率时返回 null。 */
export function videoRatePerSecond(
  billing: VideoModelBilling,
): { min: number; max: number } | null {
  const prices = billing.resolutions
    .map((rate) => rate.yuan_per_second_min)
    .filter((price) => price > 0);
  if (prices.length === 0) return null;
  return { min: Math.min(...prices), max: Math.max(...prices) };
}

/** 价目区间短标签的数字部分：单值 "2"，区间 "2~4"。 */
export function videoRateRangeLabel(min: number, max: number): string {
  return min === max
    ? formatRateNumber(min)
    : `${formatRateNumber(min)}~${formatRateNumber(max)}`;
}

function formatRateNumber(value: number): string {
  return Number.isInteger(value)
    ? String(value)
    : String(Math.round(value * 100) / 100);
}
