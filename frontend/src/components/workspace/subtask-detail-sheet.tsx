"use client";

import {
  CheckCircleIcon,
  ChevronDown,
  ChevronRight,
  ClipboardListIcon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useSubtaskMessages } from "@/core/subagents/hooks";
import type { SubagentMessage } from "@/core/subagents/hooks";
import { useSubtaskContext } from "@/core/tasks/context";

import { MarkdownContent } from "./messages/markdown-content";

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

function ToolMessageContent({
  content,
}: {
  content: string;
}) {
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
  if (msg.role === "human") {
    return (
      <div className="bg-muted/30 rounded-lg px-3 py-2">
        <div className="text-muted-foreground mb-1 text-xs font-medium">
          任务
        </div>
        <div className="text-sm">{msg.content}</div>
      </div>
    );
  }

  if (msg.role === "ai") {
    return (
      <div className="space-y-1">
        {msg.content && (
          <div className="rounded-lg px-3 py-2">
            <MarkdownContent content={msg.content} isLoading={false} rehypePlugins={[]} />
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
        <div className="text-muted-foreground flex items-center gap-1 text-xs">
          <span className="bg-secondary rounded px-1 py-0.5 font-mono">
            {msg.name}
          </span>
        </div>
        <ToolMessageContent content={msg.content} />
      </div>
    );
  }

  return null;
}

export function SubtaskDetailSheet({ threadId }: { threadId: string }) {
  const { selectedTaskId, setSelectedTaskId } = useSubtaskContext();
  const { data, isLoading } = useSubtaskMessages(threadId, selectedTaskId);

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

  return (
    <Sheet
      open={!!selectedTaskId}
      onOpenChange={(open) => {
        if (!open) setSelectedTaskId(null);
      }}
    >
      <SheetContent side="right" className="w-[480px] sm:max-w-[540px]">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <ClipboardListIcon className="size-4" />
            <span>子任务详情</span>
            {data && (
              <>
                <StatusIcon status={data.status} />
                <span className="text-muted-foreground text-sm font-normal">
                  {data.subagent_name}
                </span>
              </>
            )}
          </SheetTitle>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-1 py-2">
          {data && (
            <div className="text-muted-foreground mb-3 flex items-center gap-2 text-xs">
              <StatusIcon status={data.status} />
              <span>{statusLabel}</span>
              <span>·</span>
              <span>{data.messages.length} 条消息</span>
            </div>
          )}

          {isLoading && (
            <div className="flex items-center justify-center py-8">
              <Loader2Icon className="size-5 animate-spin" />
            </div>
          )}

          {data && (
            <div className="flex flex-col gap-3">
              {data.messages.map((msg, i) => (
                <MessageItem key={msg.id ?? i} msg={msg} />
              ))}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
