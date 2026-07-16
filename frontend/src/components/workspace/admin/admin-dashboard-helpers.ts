export type TrendPoint = {
  label: string;
  tokens: number;
  requests: number;
  images: number;
};

export type TrendMetric = "tokens" | "requests" | "images";

export type ChartCoordinate = {
  x: number;
  y: number;
};

export type DonutSegment = {
  share: number;
  offset: number;
};

export type QuotaProgressTone = "normal" | "warning" | "exceeded" | "disabled";

export type FloatingTooltipPosition = {
  left: number;
  top: number;
};

type QuotaModelGroupPriorityItem = {
  status: "normal" | "warning" | "exceeded" | "unlimited";
  source: "scope_default" | "temporary_override";
};

export const TREND_CHART_BOUNDS = {
  left: 48,
  right: 728,
  top: 24,
  bottom: 216,
} as const;

export function buildDonutSegments(values: number[]): DonutSegment[] {
  const normalized = values.map((value) => Math.max(0, value));
  const total = normalized.reduce((sum, value) => sum + value, 0);
  let offset = 0;

  return normalized.map((value) => {
    const share = total > 0 ? (value / total) * 100 : 0;
    const segment = { share, offset };
    offset += share;
    return segment;
  });
}

export function quotaProgressTone(
  enabled: boolean,
  used: number,
  limit: number | null,
): QuotaProgressTone {
  if (!enabled) return "disabled";
  if (limit == null || limit <= 0) return "normal";

  const ratio = used / limit;
  if (ratio >= 1) return "exceeded";
  if (ratio >= 0.8) return "warning";
  return "normal";
}

export function prioritizeQuotaModelGroups<
  T extends QuotaModelGroupPriorityItem,
>(groups: readonly T[]): T[] {
  const priority = (group: T) => {
    if (group.status === "exceeded") return 0;
    if (group.status === "warning") return 1;
    if (group.source === "temporary_override") return 2;
    if (group.status === "normal") return 3;
    return 4;
  };

  return groups
    .map((group, index) => ({ group, index }))
    .sort((left, right) =>
      priority(left.group) === priority(right.group)
        ? left.index - right.index
        : priority(left.group) - priority(right.group),
    )
    .map(({ group }) => group);
}

export function placeFloatingTooltip(
  containerWidth: number,
  containerHeight: number,
  pointerX: number,
  pointerY: number,
  tooltipWidth: number,
  tooltipHeight: number,
  padding = 8,
  pointerOffset = 14,
): FloatingTooltipPosition {
  const rightPosition = pointerX + pointerOffset;
  const preferredLeft =
    rightPosition + tooltipWidth <= containerWidth - padding
      ? rightPosition
      : pointerX - tooltipWidth - pointerOffset;
  const maxLeft = Math.max(padding, containerWidth - tooltipWidth - padding);
  const maxTop = Math.max(padding, containerHeight - tooltipHeight - padding);

  return {
    left: Math.max(padding, Math.min(maxLeft, preferredLeft)),
    top: Math.max(padding, Math.min(maxTop, pointerY - tooltipHeight / 2)),
  };
}

export function buildTrendCoordinates(
  points: TrendPoint[],
  metric: TrendMetric,
): ChartCoordinate[] {
  const { left, right, top, bottom } = TREND_CHART_BOUNDS;
  const width = right - left;
  const height = bottom - top;
  const max = Math.max(...points.map((point) => point[metric]), 1);

  return points.map((point, index) => ({
    x:
      points.length === 1
        ? left + width / 2
        : left + index * (width / Math.max(1, points.length - 1)),
    y: bottom - (point[metric] / max) * height,
  }));
}

export function smoothTrendPath(coordinates: ChartCoordinate[]): string {
  if (coordinates.length === 0) return "";
  if (coordinates.length === 1)
    return `M ${coordinates[0]!.x} ${coordinates[0]!.y}`;

  return coordinates.slice(1).reduce((path, point, index) => {
    const previous = coordinates[index]!;
    const middle = (previous.x + point.x) / 2;
    return `${path} C ${middle} ${previous.y}, ${middle} ${point.y}, ${point.x} ${point.y}`;
  }, `M ${coordinates[0]!.x} ${coordinates[0]!.y}`);
}
