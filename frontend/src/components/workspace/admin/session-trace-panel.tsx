"use client";

import {
  Bot,
  ChevronDown,
  Hash,
  ImageIcon,
  MessageSquare,
  Workflow,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

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

function linePath(values: number[], width = 680, height = 260) {
  const max = Math.max(...values, 1);
  return values
    .map(
      (value, index) =>
        `${index ? "L" : "M"} ${(16 + (index / Math.max(1, values.length - 1)) * (width - 32)).toFixed(1)} ${(height - 34 - (value / max) * (height - 62)).toFixed(1)}`,
    )
    .join(" ");
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
  const series = [
    ["tokens", "Token", "#ce5a2c"],
    ["model_requests", "模型调用", "#168979"],
    ["image_generations", "生图", "#3f5f99"],
  ] as const;
  let offset = 0;
  const circumference = 301.59;
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
              {series.map(([, label, color]) => (
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
            <svg
              className="mt-3 h-72 w-full"
              viewBox="0 0 680 260"
              role="img"
              aria-label="最近30天使用趋势"
            >
              {[40, 85, 130, 175, 220].map((y) => (
                <line
                  key={y}
                  x1="16"
                  x2="664"
                  y1={y}
                  y2={y}
                  stroke="currentColor"
                  className="text-border"
                  strokeDasharray="3 6"
                />
              ))}
              {series.map(([key, label, color]) => (
                <path
                  key={key}
                  d={linePath(data.trends.map((point) => point[key]))}
                  fill="none"
                  stroke={color}
                  strokeWidth="2.5"
                  strokeLinecap="round"
                >
                  <title>{label}</title>
                </path>
              ))}
              <text
                x="16"
                y="254"
                fill="currentColor"
                className="text-muted-foreground text-[11px]"
              >
                30 天前
              </text>
              <text
                x="664"
                y="254"
                textAnchor="end"
                fill="currentColor"
                className="text-muted-foreground text-[11px]"
              >
                今天
              </text>
            </svg>
          ) : (
            <div className="text-muted-foreground grid h-72 place-items-center text-sm">
              选择用户后查看趋势
            </div>
          )}
        </section>
        <section className="bg-background rounded-lg border p-4 shadow-xs">
          <h3 className="text-sm font-medium">模型分布</h3>
          <p className="text-muted-foreground text-xs">最近 30 天 Token 占比</p>
          {data?.models.length ? (
            <div className="mt-4 flex min-h-72 items-center gap-6">
              <svg
                className="size-40 shrink-0 -rotate-90"
                viewBox="0 0 120 120"
              >
                {data.models.map((model, index) => {
                  const length = circumference * model.share;
                  const circle = (
                    <circle
                      key={model.model}
                      cx="60"
                      cy="60"
                      r="48"
                      fill="none"
                      stroke={["#315d9e", "#ce5a2c", "#6f6a5f"][index % 3]}
                      strokeWidth="16"
                      strokeDasharray={`${length} ${circumference - length}`}
                      strokeDashoffset={-offset}
                    />
                  );
                  offset += length;
                  return circle;
                })}
              </svg>
              <div className="min-w-0 flex-1 space-y-3">
                {data.models.map((model, index) => (
                  <div
                    key={model.model}
                    className="flex justify-between gap-3 text-xs"
                  >
                    <span className="truncate">
                      <i
                        className="mr-2 inline-block size-2 rounded-full"
                        style={{
                          background: ["#315d9e", "#ce5a2c", "#6f6a5f"][
                            index % 3
                          ],
                        }}
                      />
                      {model.model}
                    </span>
                    <b>{(model.share * 100).toFixed(1)}%</b>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="text-muted-foreground grid h-72 place-items-center text-sm">
              选择用户后查看模型分布
            </div>
          )}
        </section>
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
          <table className="w-full min-w-[1750px] text-sm">
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
