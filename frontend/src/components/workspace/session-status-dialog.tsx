"use client";

import { useI18n } from "@/core/i18n/hooks";
import {
  cancelSubtask,
  useSessionStatus,
  type MainSessionStatus,
  type SubtaskStatusItem,
} from "@/core/subagents/hooks";
import {
  Activity,
  CheckCircle2,
  Clock,
  ChevronLeft,
  ChevronRight,
  FileText,
  Loader2,
  Square,
  XCircle,
  AlertTriangle,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useSubtaskContext } from "@/core/tasks/context";

function statusIcon(status: string) {
  switch (status) {
    case "running":
      return <Loader2 className="size-4 animate-spin text-blue-500" />;
    case "completed":
      return <CheckCircle2 className="size-4 text-green-500" />;
    case "failed":
    case "timed_out":
      return <XCircle className="size-4 text-red-500" />;
    case "interrupted":
      return <AlertTriangle className="size-4 text-yellow-500" />;
    default:
      return <Clock className="size-4 text-muted-foreground" />;
  }
}

function statusLabel(status: string, detail?: string) {
  if (status === "running" && detail) {
    switch (detail) {
      case "waiting_for_tool":
        return "等待工具调用";
      case "waiting_for_llm":
        return "等待LLM返回";
      default:
        return "运行中";
    }
  }
  switch (status) {
    case "running":
      return "运行中";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "interrupted":
      return "已中断";
    case "timed_out":
      return "超时";
    case "idle":
      return "空闲";
    default:
      return status || "未知";
  }
}

function formatTime(iso: string | null | undefined) {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function MainSessionCard({ session }: { session: MainSessionStatus }) {
  const { t } = useI18n();
  const { setSelectedTaskId } = useSubtaskContext();
  return (
    <div className="rounded-lg border p-3">
      <div className="flex items-center gap-3">
        {statusIcon(session.status)}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">主会话</span>
            <span className="text-xs text-muted-foreground">
              {statusLabel(session.status)}
            </span>
          </div>
          <div className="text-xs text-muted-foreground mt-0.5">
            {session.run_id && (
              <span>ID: {session.run_id.slice(0, 12)}...</span>
            )}
            {session.started_at && (
              <span className="ml-2">开始: {formatTime(session.started_at)}</span>
            )}
            {session.last_updated && (
              <span className="ml-2">更新: {formatTime(session.last_updated)}</span>
            )}
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 shrink-0 text-xs"
          onClick={() => setSelectedTaskId("__main__")}
        >
          <FileText className="mr-1 size-3" />
          查看消息
        </Button>
      </div>
      {session.last_message && (
        <div className="text-xs text-muted-foreground mt-2 line-clamp-3 break-all border-t pt-2">
          {session.last_message}
        </div>
      )}
    </div>
  );
}

function SubtaskRow({
  task,
  threadId,
  onCancelled,
}: {
  task: SubtaskStatusItem;
  threadId: string;
  onCancelled?: () => void;
}) {
  const { setSelectedTaskId } = useSubtaskContext();
  const [cancelling, setCancelling] = useState(false);

  const handleCancel = async () => {
    setCancelling(true);
    try {
      const result = await cancelSubtask(task.task_id);
      if (result.cancelled) {
        onCancelled?.();
      }
    } finally {
      setCancelling(false);
    }
  };

  const handleDetail = () => setSelectedTaskId(task.task_id);

  return (
    <div className="flex items-start gap-3 rounded-lg border p-3">
      <div className="mt-0.5">{statusIcon(task.status)}</div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium truncate">
            {task.description || task.task_id.slice(0, 12)}
          </span>
          <span className="text-xs text-muted-foreground shrink-0">
            {statusLabel(task.status, task.detail)}
          </span>
        </div>
        <div className="text-xs text-muted-foreground mt-0.5">
          <span>{task.subagent_name}</span>
          {task.started_at && (
            <span className="ml-2">开始: {formatTime(task.started_at)}</span>
          )}
          {task.last_updated && (
            <span className="ml-2">更新: {formatTime(task.last_updated)}</span>
          )}
        </div>
        {task.last_message && (
          <div className="text-xs text-muted-foreground mt-1 line-clamp-3 break-all">
            {task.last_message}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        {task.status === "running" && (
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            title="停止任务"
            onClick={handleCancel}
            disabled={cancelling}
          >
            {cancelling ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Square className="size-3.5" />
            )}
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          title="查看详情"
          onClick={handleDetail}
        >
          <FileText className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}

const PAGE_SIZE_OPTIONS = [5, 10, 15, 20] as const;

export function SessionStatusButton({ threadId }: { threadId: string }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, refetch } = useSessionStatus(threadId);
  const [pageSize, setPageSize] = useState<number>(10);
  const [page, setPage] = useState(0);

  const activeCount = data?.active_subtasks.length ?? 0;
  const isRunning =
    data?.main_session.status === "running" || activeCount > 0;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-8 relative"
          title="会话状态"
        >
          <Activity
            className={`size-4 ${isRunning ? "text-blue-500" : "text-muted-foreground"}`}
          />
          {isRunning && (
            <span className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-blue-500 animate-pulse" />
          )}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>会话状态</DialogTitle>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : data ? (
          <div className="flex flex-col gap-4">
            <MainSessionCard session={data.main_session} />

            {data.active_subtasks.length > 0 && (
              <div>
                <h4 className="text-sm font-medium mb-2">
                  活跃任务 ({data.active_subtasks.length})
                </h4>
                <div className="flex flex-col gap-2">
                  {data.active_subtasks.map((t) => (
                    <SubtaskRow
                      key={t.task_id}
                      task={t}
                      threadId={threadId}
                      onCancelled={() => refetch()}
                    />
                  ))}
                </div>
              </div>
            )}

            {data.recent_subtasks.length > 0 && (
              <div>
                <h4 className="text-sm font-medium mb-2">
                  最近任务 ({data.recent_subtasks.length})
                </h4>
                <div className="flex flex-col gap-2">
                  {data.recent_subtasks
                    .slice(page * pageSize, (page + 1) * pageSize)
                    .map((t) => (
                      <SubtaskRow
                        key={t.task_id}
                        task={t}
                        threadId={threadId}
                        onCancelled={() => refetch()}
                      />
                    ))}
                </div>
                {/* Pagination controls */}
                {data.recent_subtasks.length > pageSize && (
                  <div className="mt-3 flex items-center justify-between">
                    <div className="flex items-center gap-1">
                      <span className="text-muted-foreground text-xs">每页</span>
                      <select
                        className="border-input bg-background h-7 rounded border px-1 text-xs"
                        value={pageSize}
                        onChange={(e) => {
                          setPageSize(Number(e.target.value));
                          setPage(0);
                        }}
                      >
                        {PAGE_SIZE_OPTIONS.map((n) => (
                          <option key={n} value={n}>{n}</option>
                        ))}
                      </select>
                    </div>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7"
                        disabled={page === 0}
                        onClick={() => setPage((p) => p - 1)}
                      >
                        <ChevronLeft className="size-4" />
                      </Button>
                      <span className="text-muted-foreground text-xs">
                        {page + 1} / {Math.max(1, Math.ceil(data.recent_subtasks.length / pageSize))}
                      </span>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7"
                        disabled={(page + 1) * pageSize >= data.recent_subtasks.length}
                        onClick={() => setPage((p) => p + 1)}
                      >
                        <ChevronRight className="size-4" />
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {data.active_subtasks.length === 0 &&
              data.recent_subtasks.length === 0 && (
                <div className="text-center text-sm text-muted-foreground py-4">
                  暂无子任务
                </div>
              )}
          </div>
        ) : (
          <div className="text-center text-sm text-muted-foreground py-4">
            无法获取状态
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
