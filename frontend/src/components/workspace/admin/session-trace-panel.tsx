"use client";

import {
  Bot,
  ChevronDown,
  Hash,
  ImageIcon,
  MessageSquare,
  Wrench,
  Workflow,
  X,
} from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import {
  useTraceEvents,
  useTraceRuns,
  useTraceUserOverview,
  useTraceUsers,
} from "@/core/admin/hooks";
import { getTraceMode } from "@/core/admin/session-trace";
import type {
  TraceEvent,
  TraceRun,
  TraceUserOption,
  TraceUserOverview,
} from "@/core/admin/types";

import { buildDonutSegments, smoothTrendPath } from "./admin-dashboard-helpers";

const formatNumber = (value: number) =>
  new Intl.NumberFormat("zh-CN", {
    notation: value >= 10_000 ? "compact" : "standard",
    maximumFractionDigits: 1,
  }).format(value);

const formatTokenNumber = (value: number) => {
  const divisor = value >= 1_000_000 ? 1_000_000 : 1_000;
  const unit = value >= 1_000_000 ? "M" : "K";
  return `${new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 1,
  }).format(value / divisor)}${unit}`;
};

const formatDuration = (durationMs: number) => {
  if (durationMs < 1_000) return "<1s";
  const totalSeconds = Math.round(durationMs / 1_000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  return `${Math.floor(totalSeconds / 60)}m${totalSeconds % 60}s`;
};

function statusBadge(status: string) {
  const style =
    status === "success"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : status === "running" || status === "pending"
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : "border-red-200 bg-red-50 text-red-700";
  const label =
    {
      success: "成功",
      running: "运行中",
      pending: "等待中",
      error: "失败",
      timeout: "超时",
      interrupted: "已中断",
    }[status] ?? status;
  return (
    <Badge className={style} variant="outline">
      {label}
    </Badge>
  );
}

const traceSeries = [
  { key: "tokens", label: "Token", color: "#ce5a2c" },
  { key: "model_requests", label: "模型调用", color: "#168979" },
  { key: "image_generations", label: "生图", color: "#3f5f99" },
] as const;

const traceModelColors = [
  "#315d9e",
  "#168979",
  "#ce5a2c",
  "#76589b",
  "#b54769",
  "#2f7f9d",
  "#7a8f39",
  "#9a6540",
  "#526f91",
  "#a06b35",
  "#4f8a6b",
  "#9b5f87",
];

const traceModelColor = (index: number) =>
  traceModelColors[index] ??
  `hsl(${Math.round((index * 137.508 + 210) % 360)} 48% 45%)`;

const toolTones = [
  "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300",
  "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-900 dark:bg-violet-950 dark:text-violet-300",
  "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300",
  "border-cyan-200 bg-cyan-50 text-cyan-700 dark:border-cyan-900 dark:bg-cyan-950 dark:text-cyan-300",
];

const toolTone = (name: string) => {
  const hash = [...name].reduce(
    (value, character) => value * 31 + character.charCodeAt(0),
    0,
  );
  return toolTones[Math.abs(hash) % toolTones.length];
};

function TraceTrendChart({ trends }: { trends: TraceUserOverview["trends"] }) {
  const gradientId = useId().replaceAll(":", "");
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const bounds = { left: 24, right: 656, top: 24, bottom: 256 };
  const coordinates = useMemo(
    () =>
      Object.fromEntries(
        traceSeries.map(({ key }) => {
          const max = Math.max(...trends.map((point) => point[key]), 1);
          return [
            key,
            trends.map((point, index) => ({
              x:
                trends.length === 1
                  ? (bounds.left + bounds.right) / 2
                  : bounds.left +
                    (index / Math.max(1, trends.length - 1)) *
                      (bounds.right - bounds.left),
              y:
                bounds.bottom -
                (point[key] / max) * (bounds.bottom - bounds.top),
            })),
          ];
        }),
      ) as Record<
        (typeof traceSeries)[number]["key"],
        Array<{ x: number; y: number }>
      >,
    [bounds.bottom, bounds.left, bounds.right, bounds.top, trends],
  );
  const pathFor = (key: (typeof traceSeries)[number]["key"]) =>
    smoothTrendPath(coordinates[key]);
  const activePoint = activeIndex == null ? null : trends[activeIndex];
  const activeX =
    activeIndex == null ? null : coordinates.tokens[activeIndex]?.x;
  const tooltipX =
    activeX == null ? 0 : activeX > 450 ? activeX - 210 : activeX + 12;
  const tokenPath = pathFor("tokens");
  const tokenArea = trends.length
    ? `${tokenPath} L ${coordinates.tokens.at(-1)!.x} ${bounds.bottom} L ${coordinates.tokens[0]!.x} ${bounds.bottom} Z`
    : "";
  const labelIndexes = trends.length
    ? [0, Math.floor((trends.length - 1) / 2), trends.length - 1].filter(
        (index, position, indexes) => indexes.indexOf(index) === position,
      )
    : [];

  if (!trends.length) {
    return (
      <div className="text-muted-foreground grid h-[340px] place-items-center text-sm">
        最近 30 天暂无趋势数据
      </div>
    );
  }

  return (
    <div className="mt-3 h-[340px]">
      <svg
        className="size-full select-none"
        viewBox="0 0 680 296"
        role="img"
        aria-label="最近30天使用趋势，悬停或聚焦时间节点查看详细数据"
        onPointerLeave={() => setActiveIndex(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#ce5a2c" stopOpacity="0.16" />
            <stop offset="100%" stopColor="#ce5a2c" stopOpacity="0" />
          </linearGradient>
        </defs>
        <g className="text-border" stroke="currentColor" strokeDasharray="3 6">
          {[24, 82, 140, 198, 256].map((y) => (
            <line key={y} x1={bounds.left} x2={bounds.right} y1={y} y2={y} />
          ))}
        </g>
        <path d={tokenArea} fill={`url(#${gradientId})`} />
        {traceSeries.map(({ key, color }) => (
          <g key={key}>
            <path
              d={pathFor(key)}
              fill="none"
              stroke={color}
              strokeWidth={key === "tokens" ? 3.5 : 3}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {coordinates[key].map((point, index) => (
              <circle
                key={`${key}-${trends[index]!.date}`}
                cx={point.x}
                cy={point.y}
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
        {activeX != null && activePoint ? (
          <g pointerEvents="none">
            <line
              x1={activeX}
              x2={activeX}
              y1={bounds.top}
              y2={bounds.bottom}
              stroke="var(--muted-foreground)"
              strokeDasharray="4 5"
              opacity="0.5"
            />
            <g transform={`translate(${tooltipX} 28)`}>
              <rect
                width="198"
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
                {activePoint.date}
              </text>
              {traceSeries.map(({ key, label, color }, rowIndex) => (
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
                    x="186"
                    y="9"
                    fill="var(--popover-foreground)"
                    fontSize="12"
                    fontWeight="600"
                    textAnchor="end"
                  >
                    {key === "tokens"
                      ? formatTokenNumber(activePoint[key])
                      : formatNumber(activePoint[key])}
                  </text>
                </g>
              ))}
            </g>
          </g>
        ) : null}
        {trends.map((point, index) => {
          const x = coordinates.tokens[index]!.x;
          const previousX = coordinates.tokens[index - 1]?.x ?? bounds.left;
          const nextX = coordinates.tokens[index + 1]?.x ?? bounds.right;
          const hitLeft = index === 0 ? bounds.left : (previousX + x) / 2;
          const hitRight =
            index === trends.length - 1 ? bounds.right : (x + nextX) / 2;
          return (
            <rect
              key={`hit-${point.date}`}
              x={hitLeft}
              y={bounds.top}
              width={Math.max(1, hitRight - hitLeft)}
              height={bounds.bottom - bounds.top}
              fill="transparent"
              tabIndex={0}
              role="button"
              aria-label={`${point.date}，Token ${formatTokenNumber(point.tokens)}，模型调用 ${formatNumber(point.model_requests)}，生图 ${formatNumber(point.image_generations)}`}
              onFocus={() => setActiveIndex(index)}
              onBlur={() => setActiveIndex(null)}
              onPointerEnter={() => setActiveIndex(index)}
            />
          );
        })}
        <g className="fill-muted-foreground text-[11px]">
          {labelIndexes.map((index, labelIndex) => (
            <text
              key={trends[index]!.date}
              x={coordinates.tokens[index]!.x}
              y="288"
              textAnchor={
                labelIndex === 0
                  ? "start"
                  : labelIndex === labelIndexes.length - 1
                    ? "end"
                    : "middle"
              }
            >
              {trends[index]!.date.slice(5)}
            </text>
          ))}
        </g>
      </svg>
      <div className="sr-only" aria-live="polite">
        {activePoint
          ? `${activePoint.date}，Token ${formatTokenNumber(activePoint.tokens)}，模型调用 ${formatNumber(activePoint.model_requests)}，生图 ${formatNumber(activePoint.image_generations)}`
          : ""}
      </div>
    </div>
  );
}

function TraceModelDistribution({
  models,
  hasOverview,
}: {
  models: TraceUserOverview["models"];
  hasOverview: boolean;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const segments = useMemo(
    () => buildDonutSegments(models.map((model) => model.tokens)),
    [models],
  );
  const totalTokens = models.reduce((sum, model) => sum + model.tokens, 0);
  const activeModel = activeIndex == null ? null : models[activeIndex];
  const activeSegment =
    activeIndex == null ? null : (segments[activeIndex] ?? null);

  return (
    <section className="bg-background flex min-h-[356px] flex-col rounded-lg border p-4 shadow-xs">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-medium">模型分布</h3>
          <p className="text-muted-foreground truncate text-xs">
            最近 30 天 Token 占比
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
          className="relative mt-3 flex min-h-72 flex-1 items-center gap-5"
          onPointerLeave={() => setActiveIndex(null)}
        >
          {models.length ? (
            <>
              <svg
                className="size-40 shrink-0 select-none"
                viewBox="0 0 120 120"
                role="img"
                aria-label="模型 Token 分布环形图"
              >
                <circle
                  cx="60"
                  cy="60"
                  r="44"
                  fill="none"
                  stroke="var(--muted)"
                  strokeWidth="16"
                />
                <g transform="rotate(-90 60 60)">
                  {models.map((model, index) => {
                    const segment = segments[index]!;
                    const active = activeIndex === index;
                    return (
                      <circle
                        key={model.model}
                        cx="60"
                        cy="60"
                        r="44"
                        pathLength="100"
                        fill="none"
                        stroke={traceModelColor(index)}
                        strokeWidth={active ? 20 : 16}
                        strokeDasharray={`${segment.share} ${100 - segment.share}`}
                        strokeDashoffset={-segment.offset}
                        opacity={
                          activeIndex == null || activeIndex === index
                            ? 1
                            : 0.55
                        }
                        className="cursor-pointer transition-[opacity,stroke-width] duration-150 focus:outline-none"
                        tabIndex={0}
                        role="button"
                        aria-label={`${model.model}，Token ${formatTokenNumber(model.tokens)}，占比 ${segment.share.toFixed(1)}%`}
                        onFocus={() => setActiveIndex(index)}
                        onBlur={() => setActiveIndex(null)}
                        onPointerEnter={() => setActiveIndex(index)}
                      />
                    );
                  })}
                </g>
                <text
                  x="60"
                  y="56"
                  textAnchor="middle"
                  fill="var(--muted-foreground)"
                  fontSize="8"
                >
                  Token 总量
                </text>
                <text
                  x="60"
                  y="70"
                  textAnchor="middle"
                  fill="var(--foreground)"
                  fontSize="12"
                  fontWeight="700"
                >
                  {formatTokenNumber(totalTokens)}
                </text>
              </svg>
              <div className="max-h-48 min-w-0 flex-1 space-y-2 overflow-y-auto overscroll-contain pr-2">
                {models.map((model, index) => (
                  <button
                    key={model.model}
                    type="button"
                    className={`focus-visible:ring-ring grid min-h-9 w-full grid-cols-[10px_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none ${activeIndex === index ? "bg-accent" : "hover:bg-accent"}`}
                    onFocus={() => setActiveIndex(index)}
                    onBlur={() => setActiveIndex(null)}
                    onPointerEnter={() => setActiveIndex(index)}
                  >
                    <span
                      className="size-2.5 rounded-full"
                      style={{
                        backgroundColor: traceModelColor(index),
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
                    <div className="text-muted-foreground flex justify-between gap-4">
                      <span>Token</span>
                      <b className="text-popover-foreground tabular-nums">
                        {formatTokenNumber(activeModel.tokens)}
                      </b>
                    </div>
                    <div className="text-muted-foreground flex justify-between gap-4">
                      <span>Token 占比</span>
                      <b className="text-popover-foreground tabular-nums">
                        {activeSegment.share.toFixed(1)}%
                      </b>
                    </div>
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <div className="text-muted-foreground grid size-full place-items-center text-sm">
              {hasOverview
                ? "最近 30 天暂无模型用量"
                : "选择用户后查看模型分布"}
            </div>
          )}
        </div>
      ) : (
        <div className="mt-3 h-80 overflow-auto overscroll-contain pr-1">
          <table className="w-full min-w-[360px] text-sm">
            <thead className="text-muted-foreground bg-background sticky top-0 z-10 border-b text-left text-xs">
              <tr>
                <th className="py-2 font-medium">模型</th>
                <th className="font-medium">Token</th>
                <th className="text-right font-medium">占比</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model, index) => (
                <tr
                  key={model.model}
                  className="hover:bg-accent border-b transition-colors last:border-0"
                >
                  <td className="py-3 font-medium">{model.model}</td>
                  <td className="tabular-nums">
                    {formatTokenNumber(model.tokens)}
                  </td>
                  <td className="text-right tabular-nums">
                    {segments[index]!.share.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!models.length ? (
            <div className="text-muted-foreground py-10 text-center text-sm">
              {hasOverview
                ? "最近 30 天暂无模型用量"
                : "选择用户后查看模型分布"}
            </div>
          ) : null}
        </div>
      )}
      <div className="sr-only" aria-live="polite">
        {activeModel && activeSegment
          ? `${activeModel.model}，Token ${formatTokenNumber(activeModel.tokens)}，占比 ${activeSegment.share.toFixed(1)}%`
          : ""}
      </div>
    </section>
  );
}

function UserOverview({
  data,
  loading,
}: {
  data?: TraceUserOverview;
  loading: boolean;
}) {
  const summary = data?.summary;
  const kpis = [
    {
      label: "会话",
      value: summary?.thread_count,
      icon: MessageSquare,
      tone: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
    },
    {
      label: "Run",
      value: summary?.run_count,
      icon: Workflow,
      tone: "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
    },
    {
      label: "Token",
      value: summary?.total_tokens,
      icon: Hash,
      tone: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
    },
    {
      label: "模型调用",
      value: summary?.model_requests,
      icon: Bot,
      tone: "bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300",
    },
    {
      label: "生图",
      value: summary?.image_generations,
      icon: ImageIcon,
      tone: "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
    },
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {kpis.map(({ label, value, icon: Icon, tone }) => (
          <div
            key={label}
            className="bg-background rounded-lg border p-4 shadow-xs"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="text-muted-foreground text-xs">{label}</span>
              <span
                className={`grid size-8 place-items-center rounded-md ${tone}`}
              >
                <Icon className="size-4" aria-hidden="true" />
              </span>
            </div>
            <div className="mt-2 text-2xl font-medium tabular-nums">
              {loading
                ? "…"
                : value == null
                  ? "-"
                  : label === "Token"
                    ? formatTokenNumber(Number(value))
                    : formatNumber(Number(value))}
            </div>
          </div>
        ))}
      </div>
      <div className="grid gap-3 lg:grid-cols-[1.4fr_1fr]">
        <section className="bg-background rounded-lg border p-4 shadow-xs">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h3 className="text-sm font-medium">最近 30 天使用趋势</h3>
              <p className="text-muted-foreground text-xs">
                按天聚合，三项指标使用独立量纲
              </p>
            </div>
            <div className="text-muted-foreground flex gap-3 text-xs">
              {traceSeries.map(({ label, color }) => (
                <span key={label} className="flex items-center gap-1">
                  <i
                    className="size-2 rounded-full"
                    style={{ background: color }}
                  />
                  {label}
                </span>
              ))}
            </div>
          </div>
          {data ? (
            <TraceTrendChart trends={data.trends} />
          ) : (
            <div className="text-muted-foreground grid h-[340px] place-items-center text-sm">
              选择用户后查看趋势
            </div>
          )}
        </section>
        <TraceModelDistribution
          models={data?.models ?? []}
          hasOverview={Boolean(data)}
        />
      </div>
    </div>
  );
}

function EventDetail({
  event,
  expandAll,
}: {
  event: TraceEvent;
  expandAll: boolean;
}) {
  const content =
    typeof event.content === "string"
      ? event.content
      : JSON.stringify(event.content, null, 2);
  return (
    <details
      className="bg-background rounded-md border shadow-xs"
      open={expandAll ? true : undefined}
    >
      <summary className="hover:bg-muted cursor-pointer list-none px-3 py-2.5 text-sm transition-colors">
        <b>{event.event_type}</b>
        <span className="text-muted-foreground ml-2 text-xs">
          #{event.seq} · {event.category}
        </span>
      </summary>
      <pre className="text-foreground mx-3 mb-3 overflow-auto rounded border bg-white p-3 text-xs whitespace-pre-wrap shadow-xs dark:bg-zinc-950">
        {content || JSON.stringify(event.metadata, null, 2)}
      </pre>
    </details>
  );
}

function RunDrawer({
  run,
  onClose,
}: {
  run: TraceRun | null;
  onClose: () => void;
}) {
  const [expandAll, setExpandAll] = useState(true);
  const events = useTraceEvents(
    run?.run_id ?? null,
    run?.thread_id ?? null,
    Boolean(run),
  );
  return (
    <Sheet open={Boolean(run)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="bg-muted w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Run 执行详情</SheetTitle>
          <SheetDescription className="font-mono">
            {run?.run_id}
          </SheetDescription>
        </SheetHeader>
        {run ? (
          <div className="space-y-5 px-4 pb-8">
            <div className="grid grid-cols-2 overflow-hidden rounded-md border md:grid-cols-3">
              {[
                ["状态", run.status],
                ["模型", run.model_name ?? "-"],
                [
                  "耗时",
                  run.duration_ms == null
                    ? "-"
                    : `${Math.round(run.duration_ms / 1000)} 秒`,
                ],
                ["Token", formatNumber(run.total_tokens)],
                ["模型调用", run.llm_call_count],
                ["生图", run.image_generation_count],
              ].map(([label, value]) => (
                <div
                  key={label}
                  className="bg-background border-r border-b p-3"
                >
                  <div className="text-muted-foreground text-xs">{label}</div>
                  <div className="mt-1 text-sm font-medium">{value}</div>
                </div>
              ))}
            </div>
            <section className="bg-background overflow-hidden rounded-md border shadow-xs">
              <div className="bg-muted flex items-center justify-between gap-3 border-b px-3 py-2">
                <div className="flex items-center gap-2 text-xs font-medium">
                  <Wrench className="size-3.5 text-blue-600 dark:text-blue-400" />
                  工具调用概览
                </div>
                <span className="text-muted-foreground text-xs tabular-nums">
                  {events.data
                    ? `共 ${events.data.tool_summary.total_calls} 次`
                    : "-"}
                </span>
              </div>
              <div className="min-h-16 p-3">
                {events.isLoading ? (
                  <p className="text-muted-foreground text-sm">
                    正在加载工具调用…
                  </p>
                ) : events.isError ? (
                  <p className="text-destructive text-sm">
                    工具调用概览加载失败
                  </p>
                ) : events.data?.tool_summary.tools.length ? (
                  <div className="flex flex-wrap gap-2">
                    {events.data.tool_summary.tools.map((tool) => (
                      <span
                        key={tool.name}
                        className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium ${toolTone(tool.name)}`}
                      >
                        <Wrench className="size-3" aria-hidden="true" />
                        <span className="font-mono">{tool.name}</span>
                        <span className="tabular-nums opacity-70">
                          × {tool.call_count}
                        </span>
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-muted-foreground text-sm">未触发工具</p>
                )}
                {events.data && !events.data.tool_summary.complete ? (
                  <p className="text-muted-foreground mt-2 text-xs">
                    事件数量超过展示上限，工具次数基于当前返回事件统计。
                  </p>
                ) : null}
              </div>
            </section>
            <section className="bg-background overflow-hidden rounded-md border shadow-xs">
              <div className="bg-muted border-b px-3 py-2 text-xs font-medium">
                输入
              </div>
              <div className="min-h-16 p-3 text-sm whitespace-pre-wrap">
                {run.first_human_message ?? "-"}
              </div>
            </section>
            <section className="bg-background overflow-hidden rounded-md border shadow-xs">
              <div className="bg-muted border-b px-3 py-2 text-xs font-medium">
                输出
              </div>
              <div className="min-h-16 p-3 text-sm whitespace-pre-wrap">
                {run.last_ai_message ?? run.error ?? "-"}
              </div>
            </section>
            <div>
              <div className="mb-2 flex items-center justify-between gap-3">
                <h3 className="text-sm font-medium">执行时间线</h3>
                <label className="flex items-center gap-2 text-xs">
                  <Switch checked={expandAll} onCheckedChange={setExpandAll} />
                  展开全部事件
                </label>
              </div>
              {events.isLoading ? (
                <p className="text-muted-foreground text-sm">正在加载事件…</p>
              ) : events.isError ? (
                <p className="text-destructive text-sm">事件加载失败</p>
              ) : (
                <div className="bg-muted space-y-2 rounded-md border p-3 shadow-inner">
                  {events.data?.truncated ? (
                    <p className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-700">
                      事件超过 500 条，仅展示前 500 条。
                    </p>
                  ) : null}
                  {events.data?.items.map((event) => (
                    <EventDetail
                      key={event.seq}
                      event={event}
                      expandAll={expandAll}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

export function SessionTracePanel() {
  const userPickerRef = useRef<HTMLDivElement>(null);
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [selectedUser, setSelectedUser] = useState<TraceUserOption | null>(
    null,
  );
  const [autoSelectUser, setAutoSelectUser] = useState(true);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [threadId, setThreadId] = useState("");
  const [runId, setRunId] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const [selectedRun, setSelectedRun] = useState<TraceRun | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedKeyword(keyword), 300);
    return () => window.clearTimeout(timer);
  }, [keyword]);
  useEffect(() => {
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!userPickerRef.current?.contains(event.target as Node)) {
        setUserMenuOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () =>
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, []);
  const users = useTraceUsers(debouncedKeyword);
  useEffect(() => {
    if (autoSelectUser && !selectedUser && !keyword && users.data?.items[0])
      setSelectedUser(users.data.items[0]);
  }, [autoSelectUser, keyword, selectedUser, users.data?.items]);
  const traceMode = getTraceMode(selectedUser?.user_id, threadId, runId);
  const exactMode = traceMode === "exact";
  const overview = useTraceUserOverview(
    selectedUser?.user_id ?? null,
    exactMode,
  );
  const filters = useMemo(
    () => ({
      user_id: selectedUser?.user_id,
      thread_id: threadId.trim() || undefined,
      run_id: runId.trim() || undefined,
      page,
      page_size: pageSize,
    }),
    [page, pageSize, runId, selectedUser?.user_id, threadId],
  );
  const runs = useTraceRuns(filters);
  useEffect(
    () => setPage(1),
    [pageSize, runId, selectedUser?.user_id, threadId],
  );
  const modeText = exactMode
    ? `精确追踪 · 已按 ${[threadId.trim() && "Thread ID", runId.trim() && "Run ID"].filter(Boolean).join(" + ")} 查询，仅加载 Run 列表`
    : selectedUser
      ? "用户概览 · 近 30 天统计 · 该用户最新 Run"
      : "请选择用户，或输入 Thread ID / Run ID 精确查询";
  return (
    <div className="space-y-3">
      {!exactMode ? (
        <UserOverview data={overview.data} loading={overview.isLoading} />
      ) : null}
      <section className="bg-muted/35 rounded-lg border p-3 shadow-xs">
        <div className="grid gap-3 lg:grid-cols-3">
          <div ref={userPickerRef} className="relative">
            <label className="sr-only" htmlFor="trace-user-search">
              用户 · 邮箱或 USER ID
            </label>
            <div className="relative">
              <Input
                id="trace-user-search"
                value={selectedUser ? selectedUser.email : keyword}
                placeholder="搜索邮箱或 User ID"
                onFocus={() => setUserMenuOpen(true)}
                onChange={(event) => {
                  setSelectedUser(null);
                  setAutoSelectUser(false);
                  setKeyword(event.target.value);
                  setUserMenuOpen(true);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && users.data?.items[0]) {
                    setSelectedUser(users.data.items[0]);
                    setKeyword("");
                    setUserMenuOpen(false);
                  }
                }}
                className="bg-background/75 pr-9 shadow-xs"
              />
              <button
                className="text-muted-foreground absolute top-1/2 right-2 -translate-y-1/2"
                type="button"
                onClick={() => {
                  if (selectedUser) {
                    setSelectedUser(null);
                    setAutoSelectUser(false);
                    setKeyword("");
                    setUserMenuOpen(false);
                  } else {
                    setUserMenuOpen((open) => !open);
                  }
                }}
              >
                {selectedUser ? (
                  <X className="size-4" />
                ) : (
                  <ChevronDown className="size-4" />
                )}
              </button>
            </div>
            {userMenuOpen && !selectedUser ? (
              <div className="bg-popover absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-md border p-1 shadow-lg">
                {users.data?.items.map((user) => (
                  <button
                    key={user.user_id}
                    className="hover:bg-accent block w-full rounded px-3 py-2 text-left"
                    onClick={() => {
                      setSelectedUser(user);
                      setKeyword("");
                      setUserMenuOpen(false);
                    }}
                  >
                    <span className="block text-sm font-medium">
                      {user.email}
                    </span>
                    <span className="text-muted-foreground block font-mono text-xs">
                      {user.user_id}
                    </span>
                  </button>
                ))}
                {!users.isLoading && !users.data?.items.length ? (
                  <div className="text-muted-foreground p-4 text-center text-sm">
                    未找到用户
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
          <div>
            <label className="sr-only" htmlFor="trace-thread-id">
              THREAD ID · 精确匹配
            </label>
            <Input
              id="trace-thread-id"
              className="bg-background/75 font-mono shadow-xs"
              value={threadId}
              onChange={(event) => setThreadId(event.target.value)}
              placeholder="输入 Thread ID 精确查询"
            />
          </div>
          <div>
            <label className="sr-only" htmlFor="trace-run-id">
              RUN ID · 精确匹配
            </label>
            <Input
              id="trace-run-id"
              className="bg-background/75 font-mono shadow-xs"
              value={runId}
              onChange={(event) => setRunId(event.target.value)}
              placeholder="输入 Run ID 精确查询"
            />
          </div>
        </div>
      </section>
      <div className="text-muted-foreground flex items-center gap-2 px-1 text-xs">
        <span
          className={`size-2 rounded-full ${exactMode ? "bg-blue-600" : "bg-emerald-600"}`}
        />
        {modeText}
      </div>
      <section className="bg-muted/20 overflow-hidden rounded-lg border shadow-xs">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1840px] text-sm">
            <thead className="text-muted-foreground bg-muted/60 border-b text-left text-xs">
              <tr>
                {[
                  "标题",
                  "Thread ID",
                  "Run ID",
                  "状态",
                  "Input",
                  "Output",
                  "模型",
                  "耗时",
                  "Token",
                  "模型调用",
                  "生图",
                  "创建时间",
                  "详情",
                ].map((label) => (
                  <th
                    key={label}
                    className={`px-3 py-3 font-medium ${label === "标题" ? "bg-muted sticky left-0 z-10" : ""}`}
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.data?.items.map((run, index) => (
                <tr
                  key={run.run_id}
                  className={`group hover:bg-accent cursor-pointer border-b last:border-0 ${index % 2 === 0 ? "bg-background/70" : "bg-muted/35"}`}
                  onClick={() => setSelectedRun(run)}
                >
                  <td
                    className={`group-hover:bg-accent sticky left-0 z-10 max-w-64 px-3 py-4 ${index % 2 === 0 ? "bg-background" : "bg-muted"}`}
                  >
                    <b className="block truncate" title={run.thread_title}>
                      {run.thread_title}
                    </b>
                  </td>
                  <td className="px-3 font-mono text-xs">{run.thread_id}</td>
                  <td className="px-3 font-mono text-xs">{run.run_id}</td>
                  <td className="px-3">{statusBadge(run.status)}</td>
                  <td className="max-w-72 px-3">
                    <span className="block truncate" title={run.input_preview}>
                      {run.input_preview ? run.input_preview : "-"}
                    </span>
                  </td>
                  <td className="max-w-72 px-3">
                    <span className="block truncate" title={run.output_preview}>
                      {run.output_preview
                        ? run.output_preview
                        : (run.error ?? "-")}
                    </span>
                  </td>
                  <td className="px-3">{run.model_name ?? "-"}</td>
                  <td className="px-3 whitespace-nowrap">
                    {run.duration_ms == null
                      ? "-"
                      : formatDuration(run.duration_ms)}
                  </td>
                  <td className="px-3 tabular-nums">
                    {formatTokenNumber(run.total_tokens)}
                  </td>
                  <td className="px-3 tabular-nums">{run.llm_call_count}</td>
                  <td className="px-3 tabular-nums">
                    {run.image_generation_count}
                  </td>
                  <td className="px-3 text-xs whitespace-nowrap">
                    {run.created_at
                      ? new Date(run.created_at).toLocaleString("zh-CN")
                      : "-"}
                  </td>
                  <td className="px-3 whitespace-nowrap">
                    <button
                      type="button"
                      className="text-blue-600 hover:text-blue-700 hover:underline focus-visible:underline focus-visible:outline-none dark:text-blue-400 dark:hover:text-blue-300"
                      aria-label={`查看 Run ${run.run_id} 详情`}
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelectedRun(run);
                      }}
                    >
                      详情
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!runs.isLoading && !runs.data?.items.length ? (
            <div className="text-muted-foreground py-12 text-center text-sm">
              当前查询条件下暂无运行记录
            </div>
          ) : null}
        </div>
        <div className="text-muted-foreground bg-muted/45 flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-sm">
          <span>共 {runs.data?.total ?? 0} 条运行记录</span>
          <div className="flex items-center gap-2">
            <select
              className="bg-background h-8 rounded-md border px-2"
              value={pageSize}
              onChange={(event) => setPageSize(Number(event.target.value))}
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
              disabled={page <= 1}
              onClick={() => setPage((value) => value - 1)}
            >
              上一页
            </Button>
            <span>第 {page} 页</span>
            <Button
              variant="outline"
              size="sm"
              disabled={page * pageSize >= (runs.data?.total ?? 0)}
              onClick={() => setPage((value) => value + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </section>
      <RunDrawer run={selectedRun} onClose={() => setSelectedRun(null)} />
    </div>
  );
}
