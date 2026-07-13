"use client";

import {
  BarChart3,
  ImageIcon,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Users,
  type LucideIcon,
} from "lucide-react";
import {
  type MouseEvent as ReactMouseEvent,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useAdminUsage,
  useQuotaUsers,
  useUpdateQuotaUser,
} from "@/core/admin/hooks";
import type {
  QuotaMetric,
  QuotaPeriod,
  QuotaStatus,
  QuotaUser,
  SessionMetric,
  UsageModel,
  UsageRange,
  UsageSession,
} from "@/core/admin/types";
import { useAuth } from "@/core/auth/AuthProvider";

import {
  buildDonutSegments,
  buildTrendCoordinates,
  placeFloatingTooltip,
  quotaProgressTone,
  smoothTrendPath,
  TREND_CHART_BOUNDS,
  type TrendMetric,
  type TrendPoint,
} from "./admin-dashboard-helpers";

const ranges: Array<[UsageRange, string]> = [
  ["day", "天"],
  ["week", "周"],
  ["month", "月"],
  ["custom", "自定义"],
];

const quotaPeriods: Array<[QuotaPeriod, string]> = [
  ["this_month", "本月"],
  ["last_month", "上月"],
  ["custom", "自定义月份"],
];

const quotaFilters: Array<[QuotaStatus, string]> = [
  ["all", "全部"],
  ["normal", "正常"],
  ["warning", "接近上限"],
  ["exceeded", "已超额"],
  ["unlimited", "不限额"],
];

function formatNumber(value: number | null | undefined): string {
  if (value == null) return "不限";
  if (value >= 1_000_000)
    return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)}M`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}K`;
  return String(Math.round(value));
}

function metricPercent(metric: QuotaMetric): number {
  if (!metric.enabled || metric.limit == null || metric.limit <= 0) return 0;
  return Math.min(100, Math.round((metric.used / metric.limit) * 100));
}

function statusBadge(status: QuotaUser["status"]) {
  const map = {
    normal: ["正常", "border-emerald-200 bg-emerald-50 text-emerald-700"],
    warning: ["接近上限", "border-amber-200 bg-amber-50 text-amber-700"],
    exceeded: ["已超额", "border-red-200 bg-red-50 text-red-700"],
    unlimited: ["不限额", "border-sky-200 bg-sky-50 text-sky-700"],
  } satisfies Record<QuotaUser["status"], [string, string]>;
  const [label, className] = map[status];
  return (
    <Badge className={className} variant="outline">
      {label}
    </Badge>
  );
}

function KpiCard({
  label,
  value,
  note,
  icon: Icon,
}: {
  label: string;
  value: string;
  note?: string;
  icon: LucideIcon;
}) {
  return (
    <div className="bg-background rounded-lg border p-4 shadow-xs">
      <div className="text-muted-foreground flex items-center justify-between text-xs">
        <span>{label}</span>
        <Icon className="size-4" />
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-normal">{value}</div>
      {note ? (
        <div className="text-muted-foreground mt-1 text-xs">{note}</div>
      ) : null}
    </div>
  );
}

const trendSeries: Array<{ key: TrendMetric; label: string; color: string }> = [
  { key: "tokens", label: "Token", color: "#2563eb" },
  { key: "requests", label: "模型请求", color: "#059669" },
  { key: "images", label: "生图次数", color: "#d97706" },
];

const modelColors = ["#315d9e", "#059669", "#d97706", "#76589b", "#6b7280"];

const sessionMetricConfig: Record<
  SessionMetric,
  { label: string; color: string }
> = {
  tokens: { label: "Token", color: "#d97706" },
  requests: { label: "请求", color: "#d97706" },
  images: { label: "生图", color: "#d97706" },
};

const quotaProgressColors = {
  normal: {
    track: "bg-blue-100 dark:bg-blue-950/50",
    indicator: "bg-blue-600",
  },
  warning: {
    track: "bg-orange-100 dark:bg-orange-950/50",
    indicator: "bg-orange-500",
  },
  exceeded: {
    track: "bg-red-100 dark:bg-red-950/50",
    indicator: "bg-red-600",
  },
  disabled: {
    track: "bg-gray-200 dark:bg-gray-800",
    indicator: "bg-gray-400 dark:bg-gray-600",
  },
} as const;

function formatExactNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(Math.round(value));
}

function formatUsageMetric(
  metric: TrendMetric | SessionMetric,
  value: number,
): string {
  return metric === "tokens" ? formatNumber(value) : formatExactNumber(value);
}

export function TrendChart({ points }: { points: TrendPoint[] }) {
  const gradientId = useId().replaceAll(":", "");
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const data = useMemo<TrendPoint[]>(
    () =>
      points.length
        ? points
        : [{ label: "-", tokens: 0, requests: 0, images: 0 }],
    [points],
  );
  const coordinates = useMemo(
    () =>
      Object.fromEntries(
        trendSeries.map(({ key }) => [key, buildTrendCoordinates(data, key)]),
      ) as Record<TrendMetric, ReturnType<typeof buildTrendCoordinates>>,
    [data],
  );
  const activePoint =
    activeIndex == null || !points.length ? null : data[activeIndex];
  const activeX =
    activeIndex == null ? null : coordinates.tokens[activeIndex]?.x;
  const tooltipX =
    activeX == null ? 0 : activeX > 520 ? activeX - 214 : activeX + 12;
  const tokenPath = smoothTrendPath(coordinates.tokens);
  const { left, right, top, bottom } = TREND_CHART_BOUNDS;
  const tokenArea = `${tokenPath} L ${coordinates.tokens.at(-1)?.x ?? right} ${bottom} L ${coordinates.tokens[0]?.x ?? left} ${bottom} Z`;
  const labelIndexes =
    data.length <= 3
      ? data.map((_, index) => index)
      : [0, Math.floor((data.length - 1) / 2), data.length - 1];

  return (
    <div className="mt-2 min-h-0 flex-1">
      <svg
        className="size-full select-none"
        viewBox="0 0 760 260"
        role="img"
        aria-label="用量趋势，悬停或聚焦时间节点查看详细数据"
        onPointerLeave={() => setActiveIndex(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#2563eb" stopOpacity="0.2" />
            <stop offset="100%" stopColor="#2563eb" stopOpacity="0" />
          </linearGradient>
        </defs>

        <g
          className="text-border"
          stroke="currentColor"
          strokeDasharray="3 6"
          strokeWidth="1"
        >
          {[top, top + 48, top + 96, top + 144, bottom].map((y) => (
            <line key={y} x1={left} x2={right} y1={y} y2={y} />
          ))}
        </g>
        <path d={tokenArea} fill={`url(#${gradientId})`} />

        {trendSeries.map(({ key, color }) => (
          <g key={key}>
            <path
              d={smoothTrendPath(coordinates[key])}
              fill="none"
              stroke={color}
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={key === "tokens" ? 3.5 : 3}
            />
            {coordinates[key].map((coordinate, index) => (
              <circle
                key={`${key}-${data[index]?.label ?? index}`}
                cx={coordinate.x}
                cy={coordinate.y}
                r={activeIndex === index ? 4.5 : 2.5}
                fill="var(--background)"
                stroke={color}
                strokeWidth={activeIndex === index ? 3 : 2}
                opacity={
                  activeIndex == null || activeIndex === index ? 1 : 0.55
                }
              />
            ))}
          </g>
        ))}

        {activeX != null && activePoint && (
          <g pointerEvents="none">
            <line
              x1={activeX}
              x2={activeX}
              y1={top}
              y2={bottom}
              stroke="var(--muted-foreground)"
              strokeDasharray="4 5"
              opacity="0.5"
            />
            <g transform={`translate(${tooltipX} 28)`}>
              <rect
                width="202"
                height="112"
                rx="6"
                fill="var(--popover)"
                stroke="var(--border)"
              />
              <text
                x="12"
                y="21"
                fill="var(--popover-foreground)"
                fontSize="12"
                fontWeight="600"
              >
                {activePoint.label}
              </text>
              {trendSeries.map(({ key, label, color }, rowIndex) => (
                <g key={key} transform={`translate(0 ${36 + rowIndex * 23})`}>
                  <circle cx="15" cy="5" r="3.5" fill={color} />
                  <text
                    x="26"
                    y="9"
                    fill="var(--muted-foreground)"
                    fontSize="11"
                  >
                    {label}
                  </text>
                  <text
                    x="190"
                    y="9"
                    fill="var(--popover-foreground)"
                    fontSize="12"
                    fontWeight="600"
                    textAnchor="end"
                  >
                    {formatUsageMetric(key, activePoint[key])}
                  </text>
                </g>
              ))}
            </g>
          </g>
        )}

        {points.length > 0 &&
          data.map((point, index) => {
            const x = coordinates.tokens[index]!.x;
            const previousX = coordinates.tokens[index - 1]?.x ?? left;
            const nextX = coordinates.tokens[index + 1]?.x ?? right;
            const hitLeft = index === 0 ? left : (previousX + x) / 2;
            const hitRight =
              index === data.length - 1 ? right : (x + nextX) / 2;
            const ariaLabel = `${point.label}，Token ${formatUsageMetric("tokens", point.tokens)}，模型请求 ${formatUsageMetric("requests", point.requests)}，生图次数 ${formatUsageMetric("images", point.images)}`;
            return (
              <rect
                key={`hit-${point.label}-${index}`}
                x={hitLeft}
                y={top}
                width={Math.max(1, hitRight - hitLeft)}
                height={bottom - top}
                fill="transparent"
                tabIndex={0}
                role="button"
                aria-label={ariaLabel}
                onFocus={() => setActiveIndex(index)}
                onBlur={() => setActiveIndex(null)}
                onPointerEnter={() => setActiveIndex(index)}
              />
            );
          })}

        <g className="fill-muted-foreground text-[11px]">
          {labelIndexes.map((index, labelIndex) => (
            <text
              key={`${data[index]!.label}-${index}`}
              x={coordinates.tokens[index]!.x}
              y="248"
              textAnchor={
                labelIndex === 0
                  ? "start"
                  : labelIndex === labelIndexes.length - 1
                    ? "end"
                    : "middle"
              }
            >
              {data[index]!.label}
            </text>
          ))}
        </g>
      </svg>
      <div className="sr-only" aria-live="polite">
        {activePoint
          ? `${activePoint.label}，Token ${formatUsageMetric("tokens", activePoint.tokens)}，模型请求 ${formatUsageMetric("requests", activePoint.requests)}，生图次数 ${formatUsageMetric("images", activePoint.images)}`
          : ""}
      </div>
    </div>
  );
}

export function ModelDistribution({
  models,
  isLoading,
}: {
  models: UsageModel[];
  isLoading: boolean;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const segments = useMemo(
    () => buildDonutSegments(models.map((model) => model.tokens)),
    [models],
  );
  const totalTokens = useMemo(
    () => models.reduce((sum, model) => sum + model.tokens, 0),
    [models],
  );
  const activeModel = activeIndex == null ? null : models[activeIndex];
  const activeSegment =
    activeIndex == null ? null : (segments[activeIndex] ?? null);

  return (
    <section className="bg-background flex h-[380px] min-h-0 flex-col overflow-hidden rounded-lg border p-4 shadow-xs">
      <div className="flex shrink-0 items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="font-medium">模型分布</h2>
          <p className="text-muted-foreground truncate text-sm">
            仅展示 LLM 模型，按 Token 占比
          </p>
        </div>
        <div className="flex shrink-0 rounded-md border p-1">
          {(["chart", "table"] as const).map((key) => (
            <Button
              key={key}
              size="sm"
              variant={view === key ? "secondary" : "ghost"}
              onClick={() => {
                setActiveIndex(null);
                setView(key);
              }}
            >
              {key === "chart" ? "环形图" : "表格"}
            </Button>
          ))}
        </div>
      </div>

      {view === "chart" ? (
        <div
          className="relative mt-2 grid min-h-0 flex-1 grid-cols-1 items-center gap-2 overflow-y-auto sm:grid-cols-[minmax(170px,0.9fr)_minmax(150px,1.1fr)] sm:overflow-hidden"
          onPointerLeave={() => setActiveIndex(null)}
        >
          {models.length ? (
            <>
              <svg
                className="mx-auto aspect-square w-full max-w-[216px] shrink-0 select-none"
                viewBox="0 0 200 200"
                role="img"
                aria-label="模型 Token 分布环形图"
              >
                <circle
                  cx="100"
                  cy="100"
                  r="62"
                  fill="none"
                  stroke="var(--muted)"
                  strokeWidth="24"
                />
                <g transform="rotate(-90 100 100)">
                  {models.map((model, index) => {
                    const segment = segments[index]!;
                    const active = activeIndex === index;
                    return (
                      <circle
                        key={model.model}
                        cx="100"
                        cy="100"
                        r="62"
                        pathLength="100"
                        fill="none"
                        stroke={modelColors[index % modelColors.length]}
                        strokeWidth={active ? 29 : 24}
                        strokeDasharray={`${segment.share} ${100 - segment.share}`}
                        strokeDashoffset={-segment.offset}
                        opacity={
                          activeIndex == null || activeIndex === index
                            ? 1
                            : 0.58
                        }
                        className="cursor-pointer transition-[opacity,stroke-width] duration-150 focus:outline-none"
                        tabIndex={0}
                        role="button"
                        aria-label={`${model.model}，Token 占比 ${segment.share.toFixed(1)}%`}
                        onFocus={() => setActiveIndex(index)}
                        onBlur={() => setActiveIndex(null)}
                        onPointerEnter={() => setActiveIndex(index)}
                      />
                    );
                  })}
                </g>
                <text
                  x="100"
                  y="94"
                  textAnchor="middle"
                  fill="var(--muted-foreground)"
                  fontSize="11"
                >
                  Token 总量
                </text>
                <text
                  x="100"
                  y="115"
                  textAnchor="middle"
                  fill="var(--foreground)"
                  fontSize="18"
                  fontWeight="700"
                >
                  {formatNumber(totalTokens)}
                </text>
              </svg>

              <div className="grid max-h-[260px] min-w-0 gap-1.5 overflow-y-auto pr-1">
                {models.map((model, index) => (
                  <button
                    key={model.model}
                    type="button"
                    className={`focus-visible:ring-ring grid min-h-9 w-full grid-cols-[10px_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none ${
                      activeIndex === index ? "bg-accent" : "hover:bg-accent"
                    }`}
                    onFocus={() => setActiveIndex(index)}
                    onBlur={() => setActiveIndex(null)}
                    onPointerEnter={() => setActiveIndex(index)}
                  >
                    <span
                      className="size-2.5 rounded-full"
                      style={{
                        backgroundColor:
                          modelColors[index % modelColors.length],
                      }}
                    />
                    <span className="truncate text-xs font-medium">
                      {model.model}
                    </span>
                    <span className="text-muted-foreground text-xs tabular-nums">
                      {segments[index]!.share.toFixed(1)}%
                    </span>
                  </button>
                ))}
              </div>

              {activeModel && activeSegment ? (
                <div className="bg-popover text-popover-foreground pointer-events-none absolute bottom-2 left-2 z-10 w-[218px] rounded-md border p-3 text-xs shadow-lg">
                  <strong className="mb-2 block truncate">
                    {activeModel.model}
                  </strong>
                  <div className="space-y-1.5">
                    <TooltipRow
                      label="Token"
                      value={formatNumber(activeModel.tokens)}
                    />
                    <TooltipRow
                      label="请求次数"
                      value={
                        activeModel.requests == null
                          ? "-"
                          : formatExactNumber(activeModel.requests)
                      }
                    />
                    <TooltipRow
                      label="Run 数"
                      value={formatExactNumber(activeModel.runs)}
                    />
                    <TooltipRow
                      label="Token 占比"
                      value={`${activeSegment.share.toFixed(1)}%`}
                    />
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <div className="text-muted-foreground col-span-full grid h-full place-items-center text-sm">
              {isLoading ? "正在加载模型用量..." : "当前时间范围内暂无模型用量"}
            </div>
          )}
        </div>
      ) : (
        <div className="mt-3 min-h-0 flex-1 overflow-auto">
          <table className="w-full min-w-[560px] text-sm">
            <thead className="text-muted-foreground border-b text-left">
              <tr>
                <th className="py-2">模型</th>
                <th>请求</th>
                <th>Token</th>
                <th>Run</th>
                <th>占比</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model, index) => (
                <tr
                  key={model.model}
                  className="hover:bg-accent border-b transition-colors last:border-0"
                >
                  <td className="py-3 font-medium">{model.model}</td>
                  <td>
                    {model.requests == null
                      ? "-"
                      : formatNumber(model.requests)}
                  </td>
                  <td>{formatNumber(model.tokens)}</td>
                  <td>{formatNumber(model.runs)}</td>
                  <td>{segments[index]!.share.toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!isLoading && models.length === 0 ? (
            <div className="text-muted-foreground py-8 text-center text-sm">
              当前时间范围内暂无模型用量
            </div>
          ) : null}
        </div>
      )}

      <div className="sr-only" aria-live="polite">
        {activeModel && activeSegment
          ? `${activeModel.model}，Token ${formatNumber(activeModel.tokens)}，请求次数 ${activeModel.requests == null ? "暂无" : formatExactNumber(activeModel.requests)}，Run 数 ${formatExactNumber(activeModel.runs)}，Token 占比 ${activeSegment.share.toFixed(1)}%`
          : ""}
      </div>
    </section>
  );
}

function TooltipRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-muted-foreground flex items-center justify-between gap-4">
      <span>{label}</span>
      <b className="text-popover-foreground tabular-nums">{value}</b>
    </div>
  );
}

export function SessionUsagePanel({
  sessions,
  metric,
  isLoading,
  onMetricChange,
}: {
  sessions: UsageSession[];
  metric: SessionMetric;
  isLoading: boolean;
  onMetricChange: (metric: SessionMetric) => void;
}) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [tooltipPosition, setTooltipPosition] = useState({ left: 8, top: 8 });
  const chartRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const pointerPositionRef = useRef<{ x: number; y: number } | null>(null);
  const max = Math.max(...sessions.map((item) => item.value), 1);
  const activeSession = activeIndex == null ? null : sessions[activeIndex];
  const config = sessionMetricConfig[metric];

  const positionTooltip = (event: ReactMouseEvent<HTMLDivElement>) => {
    const chart = chartRef.current;
    if (!chart) return;

    const chartRect = chart.getBoundingClientRect();
    const pointerPosition = {
      x: event.clientX - chartRect.left,
      y: event.clientY - chartRect.top,
    };
    pointerPositionRef.current = pointerPosition;
    const position = placeFloatingTooltip(
      chartRect.width,
      chartRect.height,
      pointerPosition.x,
      pointerPosition.y,
      tooltipRef.current?.offsetWidth ?? 300,
      tooltipRef.current?.offsetHeight ?? 180,
    );
    setTooltipPosition(position);
  };

  useLayoutEffect(() => {
    const chart = chartRef.current;
    const tooltip = tooltipRef.current;
    const pointerPosition = pointerPositionRef.current;
    if (activeIndex == null || !chart || !tooltip || !pointerPosition) return;

    setTooltipPosition(
      placeFloatingTooltip(
        chart.clientWidth,
        chart.clientHeight,
        pointerPosition.x,
        pointerPosition.y,
        tooltip.offsetWidth,
        tooltip.offsetHeight,
      ),
    );
  }, [activeIndex, activeSession?.title]);

  const hideTooltip = () => {
    pointerPositionRef.current = null;
    setActiveIndex(null);
  };

  useEffect(() => {
    if (activeIndex == null) return;

    const closeOutside = (event: PointerEvent) => {
      if (!chartRef.current?.contains(event.target as Node)) {
        pointerPositionRef.current = null;
        setActiveIndex(null);
      }
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [activeIndex]);

  return (
    <section className="bg-background flex h-[460px] min-h-0 flex-col overflow-hidden rounded-lg border p-4 shadow-xs">
      <div className="flex shrink-0 flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-medium">会话用量 Top 20</h2>
          <p className="text-muted-foreground text-sm">
            当前指标：{config.label}；点击条形查看会话详情
          </p>
        </div>
        <div className="flex shrink-0 rounded-md border p-1">
          {(Object.keys(sessionMetricConfig) as SessionMetric[]).map((key) => (
            <Button
              key={key}
              size="sm"
              variant={metric === key ? "secondary" : "ghost"}
              onClick={() => {
                setActiveIndex(null);
                pointerPositionRef.current = null;
                onMetricChange(key);
              }}
            >
              {sessionMetricConfig[key].label}
            </Button>
          ))}
        </div>
      </div>

      <div ref={chartRef} className="relative mt-4 min-h-0 flex-1">
        <ScrollArea className="size-full overscroll-contain pr-3">
          <div className="space-y-1 pb-1">
            {sessions.map((item, index) => (
              <div
                key={item.thread_id}
                className={`focus-visible:ring-ring grid min-h-12 grid-cols-[28px_minmax(0,1fr)_72px] grid-rows-[auto_auto] items-center gap-x-3 gap-y-2 rounded-md px-2 py-1 text-sm transition-colors focus-visible:ring-2 focus-visible:outline-none md:grid-cols-[34px_minmax(190px,260px)_minmax(160px,1fr)_82px] md:grid-rows-1 ${
                  activeIndex === index ? "bg-accent" : "hover:bg-accent"
                }`}
                tabIndex={0}
                role="button"
                aria-label={`第 ${index + 1} 名，${item.email}，${item.title}，${config.label} ${formatUsageMetric(metric, item.value)}`}
                onClick={(event) => {
                  if (activeIndex === index) {
                    hideTooltip();
                    return;
                  }
                  positionTooltip(event);
                  setActiveIndex(index);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    hideTooltip();
                    return;
                  }
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  if (activeIndex === index) {
                    hideTooltip();
                    return;
                  }
                  pointerPositionRef.current = null;
                  const chartWidth = chartRef.current?.clientWidth ?? 316;
                  const tooltipWidth =
                    tooltipRef.current?.offsetWidth ??
                    Math.min(300, chartWidth - 16);
                  setTooltipPosition({
                    left: Math.max(8, chartWidth - tooltipWidth - 8),
                    top: 8,
                  });
                  setActiveIndex(index);
                }}
              >
                <div className="text-muted-foreground row-span-2 text-right text-xs tabular-nums md:row-span-1">
                  {String(index + 1).padStart(2, "0")}
                </div>
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium">
                    {item.email}
                  </div>
                  <div className="text-muted-foreground truncate text-xs">
                    {item.title}
                  </div>
                </div>
                <div className="bg-muted col-span-2 col-start-2 row-start-2 h-4 overflow-hidden rounded-full border md:col-span-1 md:col-start-3 md:row-start-1">
                  <div
                    className="h-full rounded-full transition-[width,filter] duration-200"
                    style={{
                      width: `${item.value ? Math.max(2, (item.value / max) * 100) : 0}%`,
                      backgroundColor: config.color,
                    }}
                  />
                </div>
                <div className="col-start-3 row-start-1 text-right text-xs font-medium tabular-nums md:col-start-4">
                  {formatNumber(item.value)}
                </div>
              </div>
            ))}
            {!isLoading && sessions.length === 0 ? (
              <div className="text-muted-foreground py-8 text-center text-sm">
                当前时间范围内暂无会话用量
              </div>
            ) : null}
          </div>
        </ScrollArea>

        {activeSession ? (
          <div
            ref={tooltipRef}
            className="bg-popover text-popover-foreground pointer-events-auto absolute z-10 flex max-h-[calc(100%-16px)] w-[300px] max-w-[calc(100%-16px)] flex-col overflow-hidden rounded-md border p-3 text-xs shadow-lg"
            style={tooltipPosition}
          >
            <strong className="block shrink-0 truncate">
              第 {activeIndex! + 1} 名 · {activeSession.email}
            </strong>
            <div className="text-muted-foreground mt-1 mb-2 max-h-20 min-h-0 overflow-y-auto overscroll-contain pr-1 leading-5 break-words whitespace-normal">
              {activeSession.title}
            </div>
            <div className="shrink-0 space-y-1.5 border-t pt-2">
              <TooltipRow
                label={config.label}
                value={formatUsageMetric(metric, activeSession.value)}
              />
              <TooltipRow
                label="占 Top 1"
                value={`${((activeSession.value / max) * 100).toFixed(1)}%`}
              />
            </div>
          </div>
        ) : null}
      </div>

      <div className="sr-only" aria-live="polite">
        {activeSession
          ? `第 ${activeIndex! + 1} 名，${activeSession.email}，${activeSession.title}，${config.label} ${formatUsageMetric(metric, activeSession.value)}`
          : ""}
      </div>
    </section>
  );
}

function QuotaUsageCell({ metric }: { metric: QuotaMetric }) {
  const percent = metricPercent(metric);
  const colors =
    quotaProgressColors[
      quotaProgressTone(metric.enabled, metric.used, metric.limit)
    ];
  return (
    <div className="min-w-40">
      <div className="flex justify-between text-xs">
        <span>{formatNumber(metric.used)}</span>
        <span className="text-muted-foreground">
          {metric.enabled ? formatNumber(metric.limit) : "关闭"}
        </span>
      </div>
      <div
        className={`mt-2 h-1.5 overflow-hidden rounded-full ${colors.track}`}
        role="progressbar"
        aria-label="额度使用进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <div
          className={`h-full rounded-full transition-[width] ${colors.indicator}`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

function QuotaEditor({
  user,
  period,
  periodStart,
  onOpenChange,
}: {
  user: QuotaUser | null;
  period: QuotaPeriod;
  periodStart?: string;
  onOpenChange: (open: boolean) => void;
}) {
  const updateQuota = useUpdateQuotaUser();
  const [draft, setDraft] = useState<QuotaUser | null>(user);

  useEffect(() => {
    setDraft(user);
  }, [user]);

  const current = draft ?? user;

  const setMetric = (
    key: "model_tokens" | "model_requests" | "image_generations",
    patch: Partial<QuotaMetric>,
  ) => {
    if (!current) return;
    setDraft({ ...current, [key]: { ...current[key], ...patch } });
  };

  const save = async () => {
    if (!current) return;
    await updateQuota.mutateAsync({
      userId: current.user_id,
      payload: {
        period,
        period_start: period === "custom" ? periodStart : undefined,
        model_tokens: {
          enabled: current.model_tokens.enabled,
          limit: current.model_tokens.limit,
        },
        model_requests: {
          enabled: current.model_requests.enabled,
          limit: current.model_requests.limit,
        },
        image_generations: {
          enabled: current.image_generations.enabled,
          limit: current.image_generations.limit,
        },
      },
    });
    toast.success("额度已保存");
    onOpenChange(false);
  };

  return (
    <Sheet open={!!user} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-md">
        <SheetHeader>
          <SheetTitle>调整额度</SheetTitle>
          <SheetDescription>{current?.email ?? ""}</SheetDescription>
        </SheetHeader>
        {current && (
          <div className="space-y-4 px-4">
            {(
              [
                ["model_tokens", "模型 Token"],
                ["model_requests", "模型请求"],
                ["image_generations", "生图次数"],
              ] as const
            ).map(([key, label]) => (
              <div key={key} className="rounded-lg border p-3">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-medium">{label}</div>
                  <Switch
                    checked={current[key].enabled}
                    onCheckedChange={(enabled) => setMetric(key, { enabled })}
                  />
                </div>
                <Input
                  className="mt-3"
                  type="number"
                  min={0}
                  disabled={!current[key].enabled}
                  value={current[key].limit ?? ""}
                  placeholder="不限额"
                  onChange={(event) =>
                    setMetric(key, {
                      limit:
                        event.target.value === ""
                          ? null
                          : Number(event.target.value),
                    })
                  }
                />
                <div className="text-muted-foreground mt-2 text-xs">
                  已用 {formatNumber(current[key].used)}，剩余{" "}
                  {formatNumber(current[key].remaining)}
                </div>
              </div>
            ))}
            <Button
              className="w-full"
              onClick={() => void save()}
              disabled={updateQuota.isPending}
            >
              保存
            </Button>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

export function AdminDashboard() {
  const { user } = useAuth();
  const [tab, setTab] = useState("overview");
  const [range, setRange] = useState<UsageRange>("month");
  const [metric, setMetric] = useState<SessionMetric>("tokens");
  const today = new Date().toISOString().slice(0, 10);
  const [usageStart, setUsageStart] = useState(today);
  const [usageEnd, setUsageEnd] = useState(today);
  const [quotaPeriod, setQuotaPeriod] = useState<QuotaPeriod>("this_month");
  const [quotaMonth, setQuotaMonth] = useState(() =>
    new Date().toISOString().slice(0, 7),
  );
  const [quotaStatus, setQuotaStatus] = useState<QuotaStatus>("all");
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [quotaPage, setQuotaPage] = useState(1);
  const [selectedUser, setSelectedUser] = useState<QuotaUser | null>(null);

  const customMonthStart = `${quotaMonth}-01`;
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedKeyword(keyword), 300);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  useEffect(() => {
    setQuotaPage(1);
  }, [debouncedKeyword, quotaPeriod, quotaStatus, quotaMonth]);

  const usage = useAdminUsage(
    range,
    metric,
    range === "custom" ? { start: usageStart, end: usageEnd } : undefined,
  );
  const quotas = useQuotaUsers({
    period: quotaPeriod,
    period_start: quotaPeriod === "custom" ? customMonthStart : undefined,
    status: quotaStatus,
    keyword: debouncedKeyword,
    page: quotaPage,
    page_size: 50,
  });

  const refreshAll = () => {
    if (tab === "overview") {
      void Promise.all([
        usage.summary.refetch(),
        usage.trends.refetch(),
        usage.sessions.refetch(),
        usage.models.refetch(),
      ]);
    } else {
      void quotas.refetch();
    }
  };

  const subtitle = useMemo(() => {
    if (tab === "quota") {
      const period = quotas.data?.period;
      return period ? `额度周期 · ${period.label} · 月度` : "额度周期";
    }
    const period = usage.summary.data?.period;
    return period ? `${period.label} · UTC+08:00` : "统计概览";
  }, [quotas.data?.period, tab, usage.summary.data?.period]);

  if (user?.system_role !== "admin") {
    return (
      <div className="text-muted-foreground p-8 text-sm">
        当前账号没有管理员看板权限。
      </div>
    );
  }

  const summary = usage.summary.data;
  const sessions = usage.sessions.data?.items ?? [];
  const models = usage.models.data?.items ?? [];
  const usageError =
    usage.summary.isError ||
    usage.trends.isError ||
    usage.sessions.isError ||
    usage.models.isError;
  const usageLoading =
    usage.summary.isLoading ||
    usage.trends.isLoading ||
    usage.sessions.isLoading ||
    usage.models.isLoading;

  return (
    <div className="bg-muted/20 min-h-screen">
      <div className="bg-background border-b px-6 py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-xl font-semibold">管理员看板</h1>
            <div className="text-muted-foreground mt-1 text-sm">{subtitle}</div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {tab === "overview" ? (
              <>
                <div className="bg-background flex rounded-md border p-1">
                  {ranges.map(([key, label]) => (
                    <Button
                      key={key}
                      variant={range === key ? "secondary" : "ghost"}
                      size="sm"
                      onClick={() => setRange(key)}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
                {range === "custom" && (
                  <div className="flex items-center gap-2">
                    <Input
                      className="w-36"
                      type="date"
                      value={usageStart}
                      max={usageEnd}
                      onChange={(event) => setUsageStart(event.target.value)}
                    />
                    <span className="text-muted-foreground text-sm">至</span>
                    <Input
                      className="w-36"
                      type="date"
                      value={usageEnd}
                      min={usageStart}
                      onChange={(event) => setUsageEnd(event.target.value)}
                    />
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="bg-background flex rounded-md border p-1">
                  {quotaPeriods.map(([key, label]) => (
                    <Button
                      key={key}
                      variant={quotaPeriod === key ? "secondary" : "ghost"}
                      size="sm"
                      onClick={() => setQuotaPeriod(key)}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
                {quotaPeriod === "custom" && (
                  <Input
                    className="w-36"
                    type="month"
                    value={quotaMonth}
                    onChange={(event) => setQuotaMonth(event.target.value)}
                  />
                )}
              </>
            )}
            <Button variant="outline" size="sm" onClick={refreshAll}>
              <RefreshCw className="size-4" />
              刷新
            </Button>
          </div>
        </div>
      </div>

      <div className="p-6">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="overview">统计概览</TabsTrigger>
            <TabsTrigger value="quota">额度管控</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="mt-5 space-y-5">
            {usageError && (
              <div className="border-destructive/40 bg-destructive/5 text-destructive rounded-md border px-4 py-3 text-sm">
                统计数据加载失败，请稍后重试
              </div>
            )}
            {usageLoading && (
              <div className="text-muted-foreground text-sm">
                正在加载统计数据...
              </div>
            )}
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
              <KpiCard
                label="模型 Token"
                value={formatNumber(summary?.total_tokens ?? 0)}
                note={`输入 ${formatNumber(summary?.total_input_tokens ?? 0)} · 输出 ${formatNumber(summary?.total_output_tokens ?? 0)}`}
                icon={BarChart3}
              />
              <KpiCard
                label="模型请求"
                value={formatNumber(summary?.model_requests ?? 0)}
                note={`${formatNumber(summary?.run_count ?? 0)} 个 run`}
                icon={SlidersHorizontal}
              />
              <KpiCard
                label="生图次数"
                value={formatNumber(summary?.image_generations ?? 0)}
                note="生成与修改合并计数"
                icon={ImageIcon}
              />
              <KpiCard
                label="活跃用户"
                value={formatNumber(summary?.active_users ?? 0)}
                note="有 run 的用户"
                icon={Users}
              />
              <KpiCard
                label="运行中任务"
                value={formatNumber(summary?.running_runs ?? 0)}
                icon={ShieldCheck}
              />
            </div>

            <div className="grid gap-5 xl:grid-cols-[1.35fr_1fr]">
              <section className="bg-background flex h-[380px] min-h-0 flex-col overflow-hidden rounded-lg border p-4 shadow-xs">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="font-medium">用量趋势</h2>
                    <p className="text-muted-foreground text-sm">
                      按{usage.trends.data?.bucket === "hour" ? "小时" : "天"}
                      聚合，生图只统计次数
                    </p>
                  </div>
                  <div className="text-muted-foreground hidden gap-4 text-xs md:flex">
                    {trendSeries.map(({ key, label, color }) => (
                      <span key={key} className="flex items-center gap-1.5">
                        <span
                          className="size-2 rounded-full"
                          style={{ backgroundColor: color }}
                        />
                        {label}
                      </span>
                    ))}
                  </div>
                </div>
                <TrendChart points={usage.trends.data?.items ?? []} />
              </section>

              <ModelDistribution
                models={models}
                isLoading={usage.models.isLoading}
              />
            </div>

            <SessionUsagePanel
              sessions={sessions}
              metric={metric}
              isLoading={usage.sessions.isLoading}
              onMetricChange={setMetric}
            />
          </TabsContent>

          <TabsContent value="quota" className="mt-5">
            <section className="bg-background rounded-lg border p-4 shadow-xs">
              {quotas.isError && (
                <div className="border-destructive/40 bg-destructive/5 text-destructive mb-4 rounded-md border px-4 py-3 text-sm">
                  额度数据加载失败，请稍后重试
                </div>
              )}
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <h2 className="font-medium">用户额度</h2>
                  <p className="text-muted-foreground text-sm">
                    Token/请求超额后拦截下一次 run；生图超额后后续生图失败
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <div className="relative">
                    <Search className="text-muted-foreground absolute top-2.5 left-2.5 size-4" />
                    <Input
                      className="w-64 pl-8"
                      placeholder="搜索用户邮箱"
                      value={keyword}
                      onChange={(event) => setKeyword(event.target.value)}
                    />
                  </div>
                  <div className="flex rounded-md border p-1">
                    {quotaFilters.map(([key, label]) => (
                      <Button
                        key={key}
                        size="sm"
                        variant={quotaStatus === key ? "secondary" : "ghost"}
                        onClick={() => setQuotaStatus(key)}
                      >
                        {label}
                      </Button>
                    ))}
                  </div>
                </div>
              </div>

              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[1100px] text-sm">
                  <thead className="text-muted-foreground border-b text-left">
                    <tr>
                      <th className="py-2 pr-4">用户</th>
                      <th className="px-4">角色</th>
                      <th className="px-5">模型 Token</th>
                      <th className="px-5">模型请求</th>
                      <th className="px-5 pr-8">生图次数</th>
                      <th className="px-8">状态</th>
                      <th className="pl-4">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(quotas.data?.items ?? []).map((item) => (
                      <tr key={item.user_id} className="border-b last:border-0">
                        <td className="py-4 pr-4 font-medium">{item.email}</td>
                        <td className="px-4">{item.role}</td>
                        <td className="px-5">
                          <QuotaUsageCell metric={item.model_tokens} />
                        </td>
                        <td className="px-5">
                          <QuotaUsageCell metric={item.model_requests} />
                        </td>
                        <td className="px-5 pr-8">
                          <QuotaUsageCell metric={item.image_generations} />
                        </td>
                        <td className="px-8">{statusBadge(item.status)}</td>
                        <td className="pl-4">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setSelectedUser(item)}
                          >
                            调整
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!quotas.isLoading && !quotas.data?.items.length && (
                  <div className="text-muted-foreground py-10 text-center text-sm">
                    当前筛选条件下暂无用户
                  </div>
                )}
                {(quotas.data?.total ?? 0) > (quotas.data?.page_size ?? 50) && (
                  <div className="mt-4 flex items-center justify-end gap-2 border-t pt-4">
                    <span className="text-muted-foreground text-sm">
                      共 {quotas.data?.total ?? 0} 位用户
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={quotaPage <= 1}
                      onClick={() =>
                        setQuotaPage((page) => Math.max(1, page - 1))
                      }
                    >
                      上一页
                    </Button>
                    <span className="text-sm">第 {quotaPage} 页</span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={
                        quotaPage * (quotas.data?.page_size ?? 50) >=
                        (quotas.data?.total ?? 0)
                      }
                      onClick={() => setQuotaPage((page) => page + 1)}
                    >
                      下一页
                    </Button>
                  </div>
                )}
              </div>
            </section>
          </TabsContent>
        </Tabs>
      </div>
      <QuotaEditor
        user={selectedUser}
        period={quotaPeriod}
        periodStart={customMonthStart}
        onOpenChange={(open) => !open && setSelectedUser(null)}
      />
    </div>
  );
}
