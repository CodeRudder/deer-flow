"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  ImageIcon,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
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
  useOverrideUserCurrentPeriod,
  useQuotaScopes,
  useQuotaUserDetail,
  useQuotaUsers,
  useRestoreUserCurrentPeriod,
  useSaveQuotaScope,
} from "@/core/admin/hooks";
import type {
  QuotaMetric,
  QuotaScope,
  QuotaScopePayload,
  QuotaStatus,
  QuotaUser,
  QuotaUserModelGroupItem,
  SessionMetric,
  UserQuotaItem,
  UsageModel,
  UsageRange,
  UsageSession,
  UsageUserRank,
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
import { SessionTracePanel } from "./session-trace-panel";
import { UserManagementPanel } from "./user-management-panel";

const ranges: Array<[UsageRange, string]> = [
  ["day", "天"],
  ["week", "周"],
  ["month", "月"],
  ["custom", "自定义"],
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
  if (!metric.enforced || metric.limit == null || metric.limit <= 0) return 0;
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
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  icon: LucideIcon;
  tone: string;
}) {
  return (
    <div className="bg-background rounded-lg border p-4 shadow-xs">
      <div className="text-muted-foreground flex items-center justify-between text-xs">
        <span>{label}</span>
        <span className={`grid size-8 place-items-center rounded-md ${tone}`}>
          <Icon className="size-4" aria-hidden="true" />
        </span>
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
    track:
      "bg-blue-100 ring-1 ring-inset ring-blue-200/70 dark:bg-blue-900/55 dark:ring-blue-700/60",
    indicator: "bg-blue-600 dark:bg-blue-400",
  },
  warning: {
    track:
      "bg-amber-100 ring-1 ring-inset ring-amber-200/70 dark:bg-amber-900/55 dark:ring-amber-700/60",
    indicator: "bg-amber-500 dark:bg-amber-400",
  },
  exceeded: {
    track:
      "bg-rose-100 ring-1 ring-inset ring-rose-200/70 dark:bg-rose-900/55 dark:ring-rose-700/60",
    indicator: "bg-red-600 dark:bg-rose-400",
  },
  disabled: {
    track:
      "bg-zinc-200 ring-1 ring-inset ring-zinc-300/70 dark:bg-zinc-700/70 dark:ring-zinc-600/70",
    indicator: "bg-zinc-400 dark:bg-zinc-400",
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

function UserRankingChart({
  title,
  users,
  color,
  labelTone,
  format,
  isLoading,
}: {
  title: string;
  users: UsageUserRank[];
  color: string;
  labelTone: string;
  format: (value: number) => string;
  isLoading: boolean;
}) {
  const [activeRank, setActiveRank] = useState<number | null>(null);
  const [tooltipPosition, setTooltipPosition] = useState({ left: 8, top: 8 });
  const chartRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const pointerPositionRef = useRef<{ x: number; y: number } | null>(null);
  const maximum = Math.max(...users.map((user) => user.value), 1);
  const activeUser = users.find((user) => user.rank === activeRank);

  const positionTooltip = (
    event: ReactMouseEvent<HTMLElement>,
    rank: number,
  ) => {
    const chart = chartRef.current;
    if (!chart) return;
    const rect = chart.getBoundingClientRect();
    const pointer = {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    };
    pointerPositionRef.current = pointer;
    setActiveRank(rank);
    setTooltipPosition(
      placeFloatingTooltip(
        rect.width,
        rect.height,
        pointer.x,
        pointer.y,
        tooltipRef.current?.offsetWidth ?? 224,
        tooltipRef.current?.offsetHeight ?? 120,
      ),
    );
  };

  useLayoutEffect(() => {
    const chart = chartRef.current;
    const tooltip = tooltipRef.current;
    const pointer = pointerPositionRef.current;
    if (activeRank == null || !chart || !tooltip || !pointer) return;
    setTooltipPosition(
      placeFloatingTooltip(
        chart.clientWidth,
        chart.clientHeight,
        pointer.x,
        pointer.y,
        tooltip.offsetWidth,
        tooltip.offsetHeight,
      ),
    );
  }, [activeRank]);

  return (
    <section className="bg-background flex h-[400px] min-h-0 flex-col overflow-hidden rounded-lg border p-4 shadow-xs">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">{title} Top 20</h3>
        <span className={`size-2.5 rounded-full ${color}`} />
      </div>
      <div
        ref={chartRef}
        className="relative mt-3 min-h-0 flex-1"
        onPointerLeave={() => {
          pointerPositionRef.current = null;
          setActiveRank(null);
        }}
      >
        <div className="[&::-webkit-scrollbar-thumb]:bg-border size-full overflow-x-auto overflow-y-hidden pb-3 [scrollbar-color:var(--border)_transparent] [scrollbar-width:thin] [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-track]:bg-transparent">
          <div className="flex h-full min-w-max items-end border-b px-3">
            {users.map((user) => (
              <article
                key={`${user.user_id}-${user.rank}`}
                className="w-24 shrink-0 px-2 focus:outline-none"
                tabIndex={0}
                onFocus={() => {
                  pointerPositionRef.current = null;
                  setTooltipPosition({ left: 8, top: 8 });
                  setActiveRank(user.rank);
                }}
                onBlur={() => setActiveRank(null)}
                onPointerEnter={(event) => positionTooltip(event, user.rank)}
                onPointerMove={(event) => positionTooltip(event, user.rank)}
              >
                <div className="relative h-[260px]">
                  {(() => {
                    const height = user.value
                      ? Math.max(3, (user.value / maximum) * 88)
                      : 0;
                    return (
                      <>
                        <span
                          className={`absolute left-1/2 z-10 -translate-x-1/2 rounded border px-1.5 py-0.5 text-[10px] leading-none font-semibold whitespace-nowrap tabular-nums shadow-xs transition-[bottom,filter] duration-300 ${labelTone} ${activeRank === user.rank ? "brightness-95 dark:brightness-110" : ""}`}
                          style={{ bottom: `calc(${height}% + 6px)` }}
                        >
                          {format(user.value)}
                        </span>
                        <div
                          className={`absolute bottom-0 left-1/2 w-10 -translate-x-1/2 rounded-t-sm transition-[height,filter] duration-300 ${color} ${activeRank === user.rank ? "brightness-95 dark:brightness-110" : ""}`}
                          style={{ height: `${height}%` }}
                          role="img"
                          aria-label={`第 ${user.rank} 名，${user.email}，${title} ${format(user.value)}`}
                        />
                      </>
                    );
                  })()}
                </div>
                <div className="mt-3 truncate text-center text-xs font-medium">
                  {user.email}
                </div>
              </article>
            ))}
            {!isLoading && users.length === 0 ? (
              <div className="text-muted-foreground grid h-full w-[min(720px,80vw)] place-items-center text-sm">
                当前时间范围内暂无用户用量
              </div>
            ) : null}
            {isLoading ? (
              <div className="text-muted-foreground grid h-full w-[min(720px,80vw)] place-items-center text-sm">
                正在加载用户用量…
              </div>
            ) : null}
          </div>
        </div>
        {activeUser ? (
          <div
            ref={tooltipRef}
            className="bg-popover text-popover-foreground pointer-events-none absolute z-20 w-56 rounded-md border p-3 text-xs shadow-lg"
            style={tooltipPosition}
          >
            <strong className="mb-2 block truncate">{activeUser.email}</strong>
            <div className="text-muted-foreground flex items-center justify-between gap-4">
              <span>排名</span>
              <b className="text-popover-foreground tabular-nums">
                第 {activeUser.rank} 名
              </b>
            </div>
            <div className="text-muted-foreground mt-1.5 flex items-center justify-between gap-4">
              <span className="flex items-center gap-1.5">
                <span className={`size-2 rounded-full ${color}`} />
                {title}
              </span>
              <b className="text-popover-foreground tabular-nums">
                {format(activeUser.value)}
              </b>
            </div>
          </div>
        ) : null}
      </div>
      <div className="sr-only" aria-live="polite">
        {activeUser
          ? `第 ${activeUser.rank} 名，${activeUser.email}，${title} ${format(activeUser.value)}`
          : ""}
      </div>
    </section>
  );
}

function UserUsagePanel({
  rankings,
  isLoading,
}: {
  rankings: {
    tokens: UsageUserRank[];
    requests: UsageUserRank[];
    images: UsageUserRank[];
  };
  isLoading: boolean;
}) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="font-medium">用户用量 Top 20</h2>
        <p className="text-muted-foreground text-sm">
          三项指标分别按实际用量独立排名
        </p>
      </div>
      <div className="space-y-4">
        <UserRankingChart
          title="Token"
          users={rankings.tokens}
          color="bg-amber-500 dark:bg-amber-400"
          labelTone="border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
          format={formatNumber}
          isLoading={isLoading}
        />
        <UserRankingChart
          title="模型请求"
          users={rankings.requests}
          color="bg-emerald-600 dark:bg-emerald-400"
          labelTone="border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
          format={formatExactNumber}
          isLoading={isLoading}
        />
        <UserRankingChart
          title="生图"
          users={rankings.images}
          color="bg-blue-600 dark:bg-blue-400"
          labelTone="border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200"
          format={formatExactNumber}
          isLoading={isLoading}
        />
      </div>
    </section>
  );
}

function QuotaUsageCell({ metric }: { metric: QuotaMetric }) {
  const percent = metricPercent(metric);
  const colors =
    quotaProgressColors[
      quotaProgressTone(metric.enforced, metric.used, metric.limit)
    ];
  return (
    <div className="min-w-40">
      <div className="flex justify-between text-xs">
        <span>{formatNumber(metric.used)}</span>
        <span className="text-muted-foreground">
          {metric.enforced ? formatNumber(metric.limit) : "仅记录"}
        </span>
      </div>
      <div
        className={`mt-2 h-2 overflow-hidden rounded-full ${colors.track}`}
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

function ModelGroupUsageCell({
  group,
}: {
  group: QuotaUserModelGroupItem | undefined;
}) {
  if (!group) {
    return <span className="text-muted-foreground text-xs">-</span>;
  }

  const percent = metricPercent(group.requests);
  const colors =
    quotaProgressColors[
      quotaProgressTone(
        group.requests.enforced,
        group.requests.used,
        group.requests.limit,
      )
    ];

  return (
    <div className="min-w-40 py-1 text-xs">
      {group.requests.enforced ? (
        <>
          <div className="flex items-center justify-between gap-3 whitespace-nowrap">
            <span className="font-medium tabular-nums">
              {formatNumber(group.requests.used)} /{" "}
              {formatNumber(group.requests.limit)} 次
            </span>
            <span className="text-muted-foreground tabular-nums">
              {formatNumber(group.token_observation.used)} Token
            </span>
          </div>
          <div
            className={`mt-2 h-1.5 overflow-hidden rounded-full ${colors.track}`}
            role="progressbar"
            aria-label={`${group.name}请求额度使用进度`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
          >
            <div
              className={`h-full rounded-full transition-[width] ${colors.indicator}`}
              style={{ width: `${percent}%` }}
            />
          </div>
        </>
      ) : (
        <div className="flex items-center gap-2 whitespace-nowrap">
          <span className="text-muted-foreground tabular-nums">
            {formatNumber(group.token_observation.used)} Token
          </span>
          <span className="text-border" aria-hidden="true">
            ·
          </span>
          <span className="font-medium tabular-nums">
            {formatNumber(group.requests.used)} 次
          </span>
        </div>
      )}
      {group.source === "temporary_override" ? (
        <div className="mt-1.5">
          <Badge
            variant="outline"
            className="h-5 border-amber-200 bg-amber-50 px-1.5 text-[10px] text-amber-700"
          >
            临时调额
          </Badge>
        </div>
      ) : null}
    </div>
  );
}

function QuotaUserRow({
  item,
  modelScopes,
  quotaIsCurrent,
  onAdjust,
}: {
  item: QuotaUser;
  modelScopes: QuotaScope[];
  quotaIsCurrent: boolean;
  onAdjust: (user: QuotaUser) => void;
}) {
  return (
    <tr className="border-b align-middle last:border-0">
      <td className="py-4 pr-4 font-medium">{item.email}</td>
      <td className="px-4">{item.role}</td>
      {modelScopes.map((scope) => (
        <td key={scope.id} className="px-4">
          <ModelGroupUsageCell
            group={item.model_groups.items.find(
              (group) => group.scope_id === scope.id,
            )}
          />
        </td>
      ))}
      <td className="px-5">
        {item.image_generation?.images ? (
          <QuotaUsageCell metric={item.image_generation.images} />
        ) : (
          "-"
        )}
      </td>
      <td className="px-5">{statusBadge(item.status)}</td>
      <td className="pl-4">
        <Button
          variant="outline"
          size="sm"
          disabled={!quotaIsCurrent}
          onClick={() => onAdjust(item)}
        >
          {quotaIsCurrent ? "调整" : "历史只读"}
        </Button>
      </td>
    </tr>
  );
}

function UserQuotaItemEditor({
  userId,
  item,
}: {
  userId: string;
  item: UserQuotaItem;
}) {
  const metric = item.requests ?? item.images;
  const isModelScope = item.scope.resource_type === "model";
  const overrideQuota = useOverrideUserCurrentPeriod();
  const restoreQuota = useRestoreUserCurrentPeriod();
  const [enforced, setEnforced] = useState(metric?.enforced ?? false);
  const [limit, setLimit] = useState<number | null>(metric?.limit ?? null);
  const [reason, setReason] = useState("");

  useEffect(() => {
    setEnforced(metric?.enforced ?? false);
    setLimit(metric?.limit ?? null);
  }, [metric?.enforced, metric?.limit]);

  const save = async () => {
    await overrideQuota.mutateAsync({
      userId,
      scopeId: item.scope.id,
      payload: {
        requests:
          item.scope.resource_type === "model" ? { enforced, limit } : null,
        images:
          item.scope.resource_type === "image_generation"
            ? { enforced, limit }
            : null,
        reason: reason || undefined,
      },
    });
    toast.success(`${item.scope.name}当前周期额度已调整`);
  };

  return (
    <div className="rounded-lg border p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium">
            {item.scope.name}
            {item.source === "temporary_override" ? (
              <Badge
                variant="outline"
                className="border-amber-200 bg-amber-50 text-amber-700"
              >
                临时调额
              </Badge>
            ) : null}
          </div>
          <div className="text-muted-foreground mt-1 text-xs">
            {item.period_type === "weekly" ? "自然周" : "自然月"} ·{" "}
            {item.period.label}
          </div>
        </div>
        <label className="flex shrink-0 items-center gap-2 text-xs">
          <span className="text-muted-foreground">
            {isModelScope ? "请求拦截" : "生图拦截"}
          </span>
          <Switch checked={enforced} onCheckedChange={setEnforced} />
        </label>
      </div>

      <div
        className={`bg-muted/40 mt-4 grid overflow-hidden rounded-md ${isModelScope ? "sm:grid-cols-3" : "sm:grid-cols-2"}`}
      >
        <div className="px-4 py-3">
          <div className="text-muted-foreground text-[11px]">
            {isModelScope ? "已用请求" : "已用生图"}
          </div>
          <div className="mt-1 text-xl font-semibold tabular-nums">
            {formatExactNumber(metric?.used ?? 0)}
            <span className="text-muted-foreground ml-1 text-xs font-normal">
              次
            </span>
          </div>
        </div>
        {isModelScope ? (
          <div className="border-t px-4 py-3 sm:border-t-0 sm:border-l">
            <div className="text-muted-foreground text-[11px]">Token 观测</div>
            <div className="mt-1 text-xl font-semibold tabular-nums">
              {formatNumber(item.token_observation?.used ?? 0)}
              <span className="text-muted-foreground ml-1 text-xs font-normal">
                Token
              </span>
            </div>
          </div>
        ) : null}
        <div className="border-t px-4 py-3 sm:border-t-0 sm:border-l">
          <div className="text-muted-foreground text-[11px]">当前上限</div>
          <div className="mt-1 text-xl font-semibold tabular-nums">
            {metric?.enforced && metric.limit != null
              ? formatExactNumber(metric.limit)
              : "不拦截"}
            {metric?.enforced && metric.limit != null ? (
              <span className="text-muted-foreground ml-1 text-xs font-normal">
                次
              </span>
            ) : null}
          </div>
        </div>
      </div>

      <div className="mt-3 border-t pt-3">
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
          <label className="text-muted-foreground text-xs">
            额度上限
            <Input
              className="mt-1"
              type="number"
              min={0}
              disabled={!enforced}
              value={limit ?? ""}
              placeholder="不限额"
              onChange={(event) =>
                setLimit(
                  event.target.value === "" ? null : Number(event.target.value),
                )
              }
            />
          </label>
          <label className="text-muted-foreground text-xs">
            调额原因
            <Input
              className="mt-1"
              value={reason}
              placeholder="可选"
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <Button
            className="self-end"
            size="sm"
            onClick={() => void save()}
            disabled={overrideQuota.isPending}
          >
            保存本周期
          </Button>
        </div>
      </div>
      {item.source === "temporary_override" ? (
        <Button
          className="mt-2"
          size="sm"
          variant="ghost"
          onClick={() =>
            void restoreQuota
              .mutateAsync({ userId, scopeId: item.scope.id })
              .then(() => toast.success("已恢复模型组默认额度"))
          }
          disabled={restoreQuota.isPending}
        >
          <RotateCcw className="mr-1 size-3.5" />
          恢复默认
        </Button>
      ) : null}
    </div>
  );
}

function QuotaUserEditor({
  user,
  at,
  onOpenChange,
}: {
  user: QuotaUser | null;
  at?: string;
  onOpenChange: (open: boolean) => void;
}) {
  const detail = useQuotaUserDetail(user?.user_id ?? null, at);
  return (
    <Sheet open={!!user} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>用户当前周期额度</SheetTitle>
          <SheetDescription>
            {user?.email ?? ""} · 临时调额仅在当前周/月有效
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-3 px-4 pb-6">
          {detail.isLoading ? (
            <div className="text-muted-foreground py-8 text-center text-sm">
              正在加载额度…
            </div>
          ) : null}
          {detail.data?.items.map((item) => (
            <UserQuotaItemEditor
              key={item.scope.id}
              userId={detail.data.user.user_id}
              item={item}
            />
          ))}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function QuotaScopeEditor({
  scope,
  open,
  onOpenChange,
}: {
  scope: QuotaScope | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const saveScope = useSaveQuotaScope();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [resourceType, setResourceType] = useState<
    "model" | "image_generation"
  >("model");
  const [exact, setExact] = useState("");
  const [prefix, setPrefix] = useState("");
  const [periodType, setPeriodType] = useState<"weekly" | "monthly">("weekly");
  const [enforced, setEnforced] = useState(false);
  const [limit, setLimit] = useState<number | null>(null);
  const [enabled, setEnabled] = useState(true);

  useEffect(() => {
    setCode(scope?.code ?? "");
    setName(scope?.name ?? "");
    setResourceType(scope?.resource_type ?? "model");
    setExact((scope?.match_rules.exact ?? []).join(", "));
    setPrefix((scope?.match_rules.prefix ?? []).join(", "));
    setPeriodType(scope?.default_policy.period_type ?? "weekly");
    const policy =
      scope?.default_policy.requests ?? scope?.default_policy.images ?? null;
    setEnforced(policy?.enforced ?? false);
    setLimit(policy?.limit ?? null);
    setEnabled(scope?.enabled ?? true);
  }, [scope, open]);

  const splitRules = (value: string) =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

  const save = async () => {
    const isImage = resourceType === "image_generation";
    const payload: QuotaScopePayload = {
      ...(scope ? {} : { code, resource_type: resourceType }),
      name,
      match_rules: isImage
        ? { exact: [], prefix: [] }
        : { exact: splitRules(exact), prefix: splitRules(prefix) },
      default_policy: {
        period_type: periodType,
        requests: isImage ? null : { enforced, limit },
        images: isImage ? { enforced, limit } : null,
      },
      enabled,
    };
    await saveScope.mutateAsync({ scopeId: scope?.id, payload });
    toast.success(scope ? "额度范围已更新" : "额度范围已创建");
    onOpenChange(false);
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{scope ? "编辑额度范围" : "新增额度范围"}</SheetTitle>
          <SheetDescription>
            资源匹配与所有用户默认额度在同一处维护
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-4 px-4 pb-6">
          {!scope ? (
            <label className="block text-sm">
              资源类型
              <select
                className="bg-background mt-1 h-9 w-full rounded-md border px-3"
                value={resourceType}
                onChange={(event) => {
                  const value = event.target.value as
                    | "model"
                    | "image_generation";
                  setResourceType(value);
                  if (value === "image_generation") {
                    setCode("image_generation");
                    setName((current) => current || "生图资源");
                  } else if (code === "image_generation") {
                    setCode("");
                  }
                }}
              >
                <option value="model">模型组</option>
                <option value="image_generation">生图资源</option>
              </select>
            </label>
          ) : null}
          {resourceType === "model" ? (
            <>
              <label className="block text-sm">
                稳定标识
                <Input
                  className="mt-1"
                  value={code}
                  disabled={Boolean(scope)}
                  placeholder="claude_advanced"
                  onChange={(event) => setCode(event.target.value)}
                />
              </label>
              <label className="block text-sm">
                精确模型（models[].model，逗号分隔）
                <Input
                  className="mt-1"
                  value={exact}
                  onChange={(event) => setExact(event.target.value)}
                />
              </label>
              <label className="block text-sm">
                模型前缀（逗号分隔）
                <Input
                  className="mt-1"
                  value={prefix}
                  onChange={(event) => setPrefix(event.target.value)}
                />
              </label>
            </>
          ) : null}
          <label className="block text-sm">
            显示名称
            <Input
              className="mt-1"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              统计周期
              <select
                className="bg-background mt-1 h-9 w-full rounded-md border px-3"
                value={periodType}
                onChange={(event) =>
                  setPeriodType(event.target.value as "weekly" | "monthly")
                }
              >
                <option value="weekly">自然周</option>
                <option value="monthly">自然月</option>
              </select>
            </label>
            <label className="block text-sm">
              每用户默认上限
              <Input
                className="mt-1"
                type="number"
                min={0}
                disabled={!enforced}
                value={limit ?? ""}
                placeholder="不限额"
                onChange={(event) =>
                  setLimit(
                    event.target.value === ""
                      ? null
                      : Number(event.target.value),
                  )
                }
              />
            </label>
          </div>
          <div className="flex items-center justify-between rounded-lg border p-3">
            <div>
              <div className="text-sm font-medium">
                {resourceType === "image_generation"
                  ? "按生图次数拦截"
                  : "按模型请求次数拦截"}
              </div>
              <div className="text-muted-foreground text-xs">
                {resourceType === "image_generation"
                  ? "派发到供应商后即累计，供应商报错也保留"
                  : "Token 只累计观测，不参与拦截"}
              </div>
            </div>
            <Switch checked={enforced} onCheckedChange={setEnforced} />
          </div>
          <div className="flex items-center justify-between rounded-lg border p-3">
            <span className="text-sm font-medium">
              启用{resourceType === "image_generation" ? "生图额度" : "模型组"}
            </span>
            <Switch checked={enabled} onCheckedChange={setEnabled} />
          </div>
          <Button
            className="w-full"
            onClick={() => void save()}
            disabled={saveScope.isPending || !name || (!scope && !code)}
          >
            保存
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

export function AdminDashboard() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState("overview");
  const [range, setRange] = useState<UsageRange>("month");
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const [usageStart, setUsageStart] = useState(today);
  const [usageEnd, setUsageEnd] = useState(today);
  const [quotaAt, setQuotaAt] = useState(today);
  const [quotaSection, setQuotaSection] = useState("scopes");
  const [quotaStatus, setQuotaStatus] = useState<QuotaStatus>("all");
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [quotaPage, setQuotaPage] = useState(1);
  const [quotaPageSize, setQuotaPageSize] = useState(15);
  const [selectedUser, setSelectedUser] = useState<QuotaUser | null>(null);
  const [scopeEditorOpen, setScopeEditorOpen] = useState(false);
  const [selectedScope, setSelectedScope] = useState<QuotaScope | null>(null);
  const quotaIsCurrent = quotaAt === today;

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedKeyword(keyword), 300);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  useEffect(() => {
    setQuotaPage(1);
  }, [debouncedKeyword, quotaStatus, quotaAt]);

  const usage = useAdminUsage(
    range,
    range === "custom" ? { start: usageStart, end: usageEnd } : undefined,
  );
  const quotas = useQuotaUsers({
    at: quotaAt,
    status: quotaStatus,
    keyword: debouncedKeyword,
    page: quotaPage,
    page_size: quotaPageSize,
  });
  const quotaScopes = useQuotaScopes(true);

  const refreshAll = () => {
    if (tab === "overview") {
      void Promise.all([
        usage.summary.refetch(),
        usage.trends.refetch(),
        usage.users.refetch(),
        usage.models.refetch(),
      ]);
    } else if (tab === "quota") {
      void Promise.all([quotas.refetch(), quotaScopes.refetch()]);
    } else if (tab === "trace") {
      void queryClient.invalidateQueries({
        queryKey: ["admin", "session-traces"],
      });
    } else {
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
    }
  };

  const subtitle = useMemo(() => {
    if (tab === "quota") {
      return `额度范围 · 参考日期 ${quotas.data?.reference_at ?? quotaAt}`;
    }
    if (tab === "trace")
      return "会话追踪 · 用户概览近 30 天 · 全部运行记录按创建时间倒序";
    if (tab === "users") return "用户管理 · 默认展示可用账号";
    const period = usage.summary.data?.period;
    return period ? `${period.label} · UTC+08:00` : "统计概览";
  }, [quotaAt, quotas.data?.reference_at, tab, usage.summary.data?.period]);

  if (user?.system_role !== "admin") {
    return (
      <div className="text-muted-foreground p-8 text-sm">
        当前账号没有管理员看板权限。
      </div>
    );
  }

  const summary = usage.summary.data;
  const usageRankings = usage.users.data?.rankings ?? {
    tokens: [],
    requests: [],
    images: [],
  };
  const models = usage.models.data?.items ?? [];
  const enabledModelQuotaScopes = (quotaScopes.data?.items ?? []).filter(
    (scope) => scope.enabled && scope.resource_type === "model",
  );
  const usageError =
    usage.summary.isError ||
    usage.trends.isError ||
    usage.users.isError ||
    usage.models.isError;
  const usageLoading =
    usage.summary.isLoading ||
    usage.trends.isLoading ||
    usage.users.isLoading ||
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
            ) : tab === "quota" ? (
              <label className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">参考日期</span>
                <Input
                  className="w-40"
                  type="date"
                  value={quotaAt}
                  onChange={(event) => setQuotaAt(event.target.value)}
                />
              </label>
            ) : null}
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
            <TabsTrigger value="trace">会话追踪</TabsTrigger>
            <TabsTrigger value="users">用户管理</TabsTrigger>
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
                tone="bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
              />
              <KpiCard
                label="模型请求"
                value={formatNumber(summary?.model_requests ?? 0)}
                note={`${formatNumber(summary?.run_count ?? 0)} 个 run`}
                icon={SlidersHorizontal}
                tone="bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
              />
              <KpiCard
                label="生图次数"
                value={formatNumber(summary?.image_generations ?? 0)}
                note="生成与修改合并计数"
                icon={ImageIcon}
                tone="bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300"
              />
              <KpiCard
                label="活跃用户"
                value={formatNumber(summary?.active_users ?? 0)}
                note="有 run 的用户"
                icon={Users}
                tone="bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
              />
              <KpiCard
                label="运行中任务"
                value={formatNumber(summary?.running_runs ?? 0)}
                icon={ShieldCheck}
                tone="bg-cyan-50 text-cyan-700 dark:bg-cyan-950 dark:text-cyan-300"
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

            <UserUsagePanel
              rankings={usageRankings}
              isLoading={usage.users.isLoading}
            />
          </TabsContent>

          <TabsContent value="quota" className="mt-5">
            <Tabs value={quotaSection} onValueChange={setQuotaSection}>
              <div className="mb-4 flex items-center justify-between gap-3">
                <TabsList>
                  <TabsTrigger value="scopes">额度范围</TabsTrigger>
                  <TabsTrigger value="users">用户额度</TabsTrigger>
                </TabsList>
                {quotaSection === "scopes" ? (
                  <Button
                    size="sm"
                    onClick={() => {
                      setSelectedScope(null);
                      setScopeEditorOpen(true);
                    }}
                  >
                    <Plus className="mr-1 size-4" />
                    新增额度范围
                  </Button>
                ) : null}
              </div>

              <TabsContent value="scopes" className="mt-0 space-y-4">
                {(quotaScopes.data?.unmatched_models.length ?? 0) > 0 ? (
                  <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                    有 {quotaScopes.data?.unmatched_models.length}{" "}
                    个模型尚未归组：
                    {quotaScopes.data?.unmatched_models
                      .map((model) => model.model)
                      .join("、")}
                  </div>
                ) : null}
                {(quotaScopes.data?.configuration_warnings.length ?? 0) > 0 ? (
                  <div className="border-destructive/40 bg-destructive/5 text-destructive rounded-lg border px-4 py-3 text-sm">
                    配置中存在重复的 models[].model，请先合并重复模型配置。
                  </div>
                ) : null}
                <div className="grid gap-4 lg:grid-cols-2">
                  {(quotaScopes.data?.items ?? []).map((scope) => {
                    const policy =
                      scope.default_policy.requests ??
                      scope.default_policy.images;
                    return (
                      <article
                        key={scope.id}
                        className="bg-background rounded-lg border p-4 shadow-xs"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <h3 className="font-medium">{scope.name}</h3>
                              <Badge variant="outline">{scope.code}</Badge>
                              {!scope.enabled ? (
                                <Badge variant="secondary">已停用</Badge>
                              ) : null}
                            </div>
                            <p className="text-muted-foreground mt-2 text-sm">
                              {scope.resource_type === "model"
                                ? `${scope.matched_models.length} 个已配置模型`
                                : "所有生图与图片编辑调用"}
                            </p>
                          </div>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setSelectedScope(scope);
                              setScopeEditorOpen(true);
                            }}
                          >
                            <Pencil className="mr-1 size-3.5" />
                            编辑
                          </Button>
                        </div>
                        <div className="mt-4 grid grid-cols-3 gap-3 text-sm">
                          <div className="bg-muted/50 rounded-md p-3">
                            <div className="text-muted-foreground text-xs">
                              周期
                            </div>
                            <div className="mt-1 font-medium">
                              {scope.default_policy.period_type === "weekly"
                                ? "自然周"
                                : "自然月"}
                            </div>
                          </div>
                          <div className="bg-muted/50 rounded-md p-3">
                            <div className="text-muted-foreground text-xs">
                              拦截
                            </div>
                            <div className="mt-1 font-medium">
                              {policy?.enforced ? "开启" : "仅记录"}
                            </div>
                          </div>
                          <div className="bg-muted/50 rounded-md p-3">
                            <div className="text-muted-foreground text-xs">
                              每用户上限
                            </div>
                            <div className="mt-1 font-medium">
                              {formatNumber(policy?.limit)}
                            </div>
                          </div>
                        </div>
                        {scope.resource_type === "model" ? (
                          <div className="text-muted-foreground mt-3 text-xs">
                            精确：{scope.match_rules.exact.join("、") || "无"} ·
                            前缀：
                            {scope.match_rules.prefix.join("、") || "无"}
                          </div>
                        ) : null}
                      </article>
                    );
                  })}
                </div>
              </TabsContent>

              <TabsContent value="users" className="mt-0">
                <section className="bg-background rounded-lg border p-4 shadow-xs">
                  {quotas.isError ? (
                    <div className="border-destructive/40 bg-destructive/5 text-destructive mb-4 rounded-md border px-4 py-3 text-sm">
                      额度数据加载失败，请稍后重试
                    </div>
                  ) : null}
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                      <h2 className="font-medium">用户额度</h2>
                      <p className="text-muted-foreground text-sm">
                        所有用户默认继承模型组配置；仅记录范围不拦截，用户临时调额单独标记
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
                            variant={
                              quotaStatus === key ? "secondary" : "ghost"
                            }
                            onClick={() => setQuotaStatus(key)}
                          >
                            {label}
                          </Button>
                        ))}
                      </div>
                    </div>
                  </div>

                  <div className="mt-4 overflow-x-auto">
                    <table
                      className="w-full text-sm"
                      style={{
                        minWidth: `${Math.max(940, 620 + enabledModelQuotaScopes.length * 190)}px`,
                      }}
                    >
                      <thead className="text-muted-foreground border-b text-left">
                        <tr>
                          <th className="py-2 pr-4">用户</th>
                          <th className="px-4">角色</th>
                          {enabledModelQuotaScopes.map((scope) => (
                            <th key={scope.id} className="px-4">
                              {scope.name}
                            </th>
                          ))}
                          <th className="px-5">生图次数</th>
                          <th className="px-5">状态</th>
                          <th className="pl-4">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(quotas.data?.items ?? []).map((item) => (
                          <QuotaUserRow
                            key={item.user_id}
                            item={item}
                            modelScopes={enabledModelQuotaScopes}
                            quotaIsCurrent={quotaIsCurrent}
                            onAdjust={setSelectedUser}
                          />
                        ))}
                      </tbody>
                    </table>
                    {!quotas.isLoading && !quotas.data?.items.length ? (
                      <div className="text-muted-foreground py-10 text-center text-sm">
                        当前筛选条件下暂无用户
                      </div>
                    ) : null}
                  </div>
                  <div className="text-muted-foreground mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4 text-sm">
                    <span>共 {quotas.data?.total ?? 0} 位用户</span>
                    <div className="flex items-center gap-2">
                      <select
                        className="bg-background h-8 rounded-md border px-2"
                        aria-label="每页用户数量"
                        value={quotaPageSize}
                        onChange={(event) => {
                          setQuotaPageSize(Number(event.target.value));
                          setQuotaPage(1);
                        }}
                      >
                        {[15, 30, 50].map((size) => (
                          <option key={size} value={size}>
                            每页 {size} 条
                          </option>
                        ))}
                      </select>
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
                          quotaPage * quotaPageSize >= (quotas.data?.total ?? 0)
                        }
                        onClick={() => setQuotaPage((page) => page + 1)}
                      >
                        下一页
                      </Button>
                    </div>
                  </div>
                </section>
              </TabsContent>
            </Tabs>
          </TabsContent>

          <TabsContent value="trace" className="mt-5">
            <SessionTracePanel />
          </TabsContent>

          <TabsContent value="users" className="mt-5">
            <UserManagementPanel />
          </TabsContent>
        </Tabs>
      </div>
      <QuotaUserEditor
        user={selectedUser}
        at={quotaAt}
        onOpenChange={(open) => !open && setSelectedUser(null)}
      />
      <QuotaScopeEditor
        scope={selectedScope}
        open={scopeEditorOpen}
        onOpenChange={(open) => {
          setScopeEditorOpen(open);
          if (!open) setSelectedScope(null);
        }}
      />
    </div>
  );
}
