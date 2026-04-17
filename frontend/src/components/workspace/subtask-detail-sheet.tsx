"use client";

import {
  CheckCircleIcon,
  ChevronDown,
  ChevronRight,
  ClipboardListIcon,
  Loader2Icon,
  MessageSquareIcon,
  XCircleIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  useMainSessionMessages,
  useSubtaskMessages,
} from "@/core/subagents/hooks";
import type { SubagentMessage } from "@/core/subagents/hooks";
import { useSubtaskContext } from "@/core/tasks/context";

import { MarkdownContent } from "./messages/markdown-content";

const MAIN_SESSION_ID = "__main__";
const MESSAGES_PER_PAGE = 100;

const MIN_SHEET_WIDTH = 360;
const MAX_SHEET_WIDTH = 960;
const DEFAULT_SHEET_WIDTH = 540;

function formatTime(ts: string): string {
  try {
    const d = new Date(ts);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
  } catch {
    return "";
  }
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "completed":
      return <CheckCircleIcon className="size-3.5 text-green-500" />;
    case "failed":
    case "interrupted":
      return <XCircleIcon className="size-3.5 text-red-500" />;
    default:
      return <Loader2Icon className="size-3.5 animate-spin" />;
  }
}

function ToolMessageContent({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  const truncated = content.length > 200;
  const display = expanded || !truncated ? content : content.slice(0) + "...";

  if (!truncated) {
    return (
      <pre className="bg-muted/50 max-h-64 overflow-auto rounded p-2 text-xs whitespace-pre-wrap">
        {content}
      </pre>
    );
  }

  return (
    <div>
      <pre
        className={`bg-muted/50 overflow-auto rounded p-2 text-xs whitespace-pre-wrap ${expanded ? "max-h-96" : "max-h-24"}`}
      >
        {display}
      </pre>
      <button
        type="button"
        className="text-muted-foreground hover:text-foreground mt-1 flex items-center gap-1 text-xs"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? (
          <ChevronDown className="size-3" />
        ) : (
          <ChevronRight className="size-3" />
        )}
        {expanded ? "收起" : `展开 (${content.length} 字符)`}
      </button>
    </div>
  );
}

function MessageItem({ msg }: { msg: SubagentMessage }) {
  const timeStr = msg.ts ? formatTime(msg.ts) : "";

  if (msg.role === "human") {
    return (
      <div className="bg-muted/30 rounded-lg px-3 py-2">
        <div className="text-muted-foreground mb-1 flex items-center gap-2 text-xs font-medium">
          <span>任务</span>
          {timeStr && <span>{timeStr}</span>}
        </div>
        <div className="text-sm">{msg.content}</div>
      </div>
    );
  }

  if (msg.role === "ai") {
    return (
      <div className="space-y-1">
        {timeStr && (
          <div className="text-muted-foreground text-xs">{timeStr}</div>
        )}
        {msg.content && (
          <div className="rounded-lg px-3 py-2">
            <MarkdownContent
              content={msg.content}
              isLoading={false}
              rehypePlugins={[]}
            />
          </div>
        )}
        {msg.tool_calls && msg.tool_calls.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {msg.tool_calls.map((tc) => (
              <span
                key={tc.id}
                className="bg-primary/10 text-primary rounded px-1.5 py-0.5 text-xs"
              >
                {tc.name}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (msg.role === "tool") {
    return (
      <div className="space-y-1">
        <div className="text-muted-foreground flex items-center gap-2 text-xs">
          <span className="bg-secondary rounded px-1 py-0.5 font-mono">
            {msg.name}
          </span>
          {timeStr && <span>{timeStr}</span>}
        </div>
        <ToolMessageContent content={msg.content} />
      </div>
    );
  }

  return null;
}

function SubtaskMessages({
  threadId,
  taskId,
}: {
  threadId: string;
  taskId: string;
}) {
  const { data, isLoading } = useSubtaskMessages(threadId, taskId);
  const bottomRef = useRef<HTMLDivElement>(null);

  const statusLabel = useMemo(() => {
    if (!data) return "";
    const map: Record<string, string> = {
      completed: "已完成",
      failed: "失败",
      interrupted: "已中断",
      running: "运行中",
    };
    return map[data.status] ?? data.status;
  }, [data]);

  useEffect(() => {
    if (data && data.messages.length > 0) {
      requestAnimationFrame(() => {
        bottomRef.current?.scrollIntoView({ behavior: "instant" });
      });
    }
  }, [data]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2Icon className="size-5 animate-spin" />
      </div>
    );
  }

  if (!data) return null;

  return (
    <>
      <div className="text-muted-foreground mb-3 flex items-center gap-2 text-xs">
        <StatusIcon status={data.status} />
        <span>{statusLabel}</span>
        <span>·</span>
        <span>{data.subagent_name}</span>
        <span>·</span>
        <span>{data.messages.length} 条消息</span>
      </div>
      <div className="flex flex-col gap-3">
        {data.messages.map((msg, i) => (
          <MessageItem key={msg.id ?? i} msg={msg} />
        ))}
        <div ref={bottomRef} />
      </div>
    </>
  );
}

function MainSessionMessages({ threadId }: { threadId: string }) {
  const [offset, setOffset] = useState(0);
  const [allMessages, setAllMessages] = useState<SubagentMessage[]>([]);
  const { data, isLoading } = useMainSessionMessages(
    threadId,
    true,
    MESSAGES_PER_PAGE,
    offset,
  );
  const bottomRef = useRef<HTMLDivElement>(null);
  const isInitialLoad = useRef(true);

  useEffect(() => {
    if (!data?.messages) return;
    if (offset === 0) {
      setAllMessages(data.messages);
      isInitialLoad.current = true;
    } else {
      setAllMessages((prev) => [...data.messages, ...prev]);
    }
  }, [data, offset]);

  useEffect(() => {
    if (isInitialLoad.current && allMessages.length > 0) {
      requestAnimationFrame(() => {
        bottomRef.current?.scrollIntoView({ behavior: "instant" });
      });
      isInitialLoad.current = false;
    }
  }, [allMessages]);

  const handleLoadMore = useCallback(() => {
    setOffset((prev) => prev + MESSAGES_PER_PAGE);
  }, []);

  const hasMore = data?.has_more ?? false;

  return (
    <>
      <div className="text-muted-foreground mb-3 flex items-center gap-2 text-xs">
        <MessageSquareIcon className="size-3.5" />
        <span>主会话消息</span>
        <span>·</span>
        <span>共 {data?.total ?? 0} 条</span>
      </div>
      {hasMore && (
        <button
          type="button"
          className="text-muted-foreground hover:text-foreground mb-2 flex w-full items-center justify-center gap-1 py-1 text-xs hover:underline"
          onClick={handleLoadMore}
          disabled={isLoading}
        >
          {isLoading ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            "加载更多消息..."
          )}
        </button>
      )}
      <div className="flex flex-col gap-3">
        {allMessages.map((msg, i) => (
          <MessageItem key={msg.id ?? `msg-${i}`} msg={msg} />
        ))}
        <div ref={bottomRef} />
      </div>
    </>
  );
}

export function SubtaskDetailSheet({ threadId }: { threadId: string }) {
  const { selectedTaskId, setSelectedTaskId } = useSubtaskContext();
  const [sheetWidth, setSheetWidth] = useState(DEFAULT_SHEET_WIDTH);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  const isMainSession = selectedTaskId === MAIN_SESSION_ID;

  // Drag-to-resize handlers
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      isDragging.current = true;
      startX.current = e.clientX;
      startWidth.current = sheetWidth;
      e.preventDefault();

      const handleMouseMove = (e: MouseEvent) => {
        if (!isDragging.current) return;
        const delta = startX.current - e.clientX;
        const newWidth = Math.min(
          MAX_SHEET_WIDTH,
          Math.max(MIN_SHEET_WIDTH, startWidth.current + delta),
        );
        setSheetWidth(newWidth);
      };

      const handleMouseUp = () => {
        isDragging.current = false;
        document.removeEventListener("mousemove", handleMouseMove);
        document.removeEventListener("mouseup", handleMouseUp);
      };

      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
    },
    [sheetWidth],
  );

  return (
    <Sheet
      open={!!selectedTaskId}
      onOpenChange={(open) => {
        if (!open) setSelectedTaskId(null);
      }}
    >
      <SheetContent
        side="right"
        className="flex flex-col p-0"
        style={{ width: `${sheetWidth}px`, maxWidth: `${MAX_SHEET_WIDTH}px` }}
      >
        {/* Drag handle */}
        <div
          className="absolute top-0 left-0 z-50 h-full w-1 cursor-col-resize hover:bg-primary/20 active:bg-primary/30"
          onMouseDown={handleMouseDown}
        />
        <SheetHeader className="shrink-0 px-6 pt-6 pb-2">
          <SheetTitle className="flex items-center gap-2">
            {isMainSession ? (
              <>
                <MessageSquareIcon className="size-4" />
                <span>主会话消息</span>
              </>
            ) : (
              <>
                <ClipboardListIcon className="size-4" />
                <span>子任务详情</span>
              </>
            )}
          </SheetTitle>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-6 py-2">
          {isMainSession ? (
            <MainSessionMessages threadId={threadId} />
          ) : selectedTaskId ? (
            <SubtaskMessages threadId={threadId} taskId={selectedTaskId} />
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
