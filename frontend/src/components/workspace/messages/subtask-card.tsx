import {
  CheckCircleIcon,
  ChevronUp,
  ClipboardListIcon,
  Loader2Icon,
  RotateCcwIcon,
  SquareIcon,
  XCircleIcon,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Streamdown } from "streamdown";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { Shimmer } from "@/components/ai-elements/shimmer";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { ShineBorder } from "@/components/ui/shine-border";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { hasToolCalls } from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { streamdownPluginsWithWordAnimation } from "@/core/streamdown";
import {
  useSubtask,
  useSubtaskContext,
  useUpdateSubtask,
} from "@/core/tasks/context";
import { explainLastToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";
import { FlipDisplay } from "../flip-display";

import { MarkdownContent } from "./markdown-content";
import { useThread } from "../messages/context";

export function SubtaskCard({
  className,
  taskId,
  threadId,
  isLoading,
}: {
  className?: string;
  taskId: string;
  threadId: string;
  isLoading: boolean;
}) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  const task = useSubtask(taskId);
  if (!task) return null;
  const updateSubtask = useUpdateSubtask();
  const { setSelectedTaskId } = useSubtaskContext();
  const { thread: streamThread } = useThread();

  const handleCancel = useCallback(async () => {
    setCancelling(true);
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/runs/subtasks/${taskId}/cancel`,
        { method: "POST" },
      );
      if (res.ok) {
        const data = await res.json();
        if (data.cancelled) {
          updateSubtask({
            id: taskId,
            status: "failed",
            error: "Cancelled by user",
          });
        }
      }
    } catch {
      // silently ignore
    } finally {
      setCancelling(false);
    }
  }, [taskId, updateSubtask]);

  const handleResume = useCallback(async () => {
    setResuming(true);
    try {
      // Update subtask status optimistically
      updateSubtask({
        id: taskId,
        status: "in_progress",
        error: undefined,
      });

      // Send resume message through the normal conversation stream
      // so the lead agent calls task(action="resume") and the frontend
      // receives streaming events.
      const message = `恢复执行子任务（${task.description}）。\n请使用 task tool 的 action="resume" 模式恢复执行：\ntask(description="${task.description}", prompt="继续执行", subagent_type="${task.subagent_type}", action="resume", task_id="${taskId}")`;

      try {
        await streamThread.submit(
          {
            messages: [{ type: "human", content: message }],
          },
          {
            threadId,
            streamSubgraphs: true,
            streamResumable: true,
            config: { recursion_limit: 1000 },
            context: {
              subagent_enabled: true,
              is_plan_mode: true,
              thinking_enabled: true,
              thread_id: threadId,
            },
          },
        );
      } catch {
        // Fallback to API if thread submit fails
        const res = await fetch(
          `${getBackendBaseURL()}/api/threads/${threadId}/subagents/${taskId}/resume`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) },
        );
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          console.error("Resume failed:", data.detail ?? res.statusText);
        }
      }
    } catch {
      // silently ignore
    } finally {
      setResuming(false);
    }
  }, [taskId, threadId, task.description, task.subagent_type, streamThread, updateSubtask]);
  const icon = useMemo(() => {
    if (task.status === "completed") {
      return <CheckCircleIcon className="size-3" />;
    } else if (task.status === "failed") {
      return <XCircleIcon className="size-3 text-red-500" />;
    } else if (task.status === "in_progress") {
      return <Loader2Icon className="size-3 animate-spin" />;
    }
  }, [task.status]);
  return (
    <ChainOfThought
      className={cn("relative w-full gap-2 rounded-lg border py-0", className)}
      open={!collapsed}
    >
      <div
        className={cn(
          "ambilight z-[-1]",
          task.status === "in_progress" ? "enabled" : "",
        )}
      ></div>
      {task.status === "in_progress" && (
        <>
          <ShineBorder
            borderWidth={1.5}
            shineColor={["#A07CFE", "#FE8FB5", "#FFBE7B"]}
          />
        </>
      )}
      <div className="bg-background/95 flex w-full flex-col rounded-lg">
        <div className="flex w-full items-center justify-between p-0.5">
          <Button
            className="w-full items-start justify-start text-left"
            variant="ghost"
            onClick={() => setCollapsed(!collapsed)}
          >
            <div className="flex w-full items-center justify-between">
              <ChainOfThoughtStep
                className="font-normal"
                label={
                  task.status === "in_progress" ? (
                    <Shimmer duration={3} spread={3}>
                      {task.description}
                    </Shimmer>
                  ) : (
                    task.description
                  )
                }
                icon={<ClipboardListIcon />}
              ></ChainOfThoughtStep>
              <div className="flex items-center gap-1">
                {collapsed && (
                  <div
                    className={cn(
                      "text-muted-foreground flex items-center gap-1 text-xs font-normal",
                      task.status === "failed" ? "text-red-500 opacity-67" : "",
                    )}
                  >
                    {icon}
                    <FlipDisplay
                      className="max-w-[420px] truncate pb-1"
                      uniqueKey={task.latestMessage?.id ?? ""}
                    >
                      {task.status === "in_progress" &&
                      task.latestMessage &&
                      hasToolCalls(task.latestMessage)
                        ? explainLastToolCall(task.latestMessage, t)
                        : t.subtasks[task.status]}
                    </FlipDisplay>
                  </div>
                )}
                {task.status === "in_progress" && !collapsed && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-6"
                    disabled={cancelling}
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmOpen(true);
                    }}
                  >
                    <SquareIcon className="size-3" />
                  </Button>
                )}
                {task.status === "failed" && !collapsed && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 text-xs"
                    disabled={resuming}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleResume();
                    }}
                  >
                    {resuming ? (
                      <Loader2Icon className="mr-1 size-3 animate-spin" />
                    ) : (
                      <RotateCcwIcon className="mr-1 size-3" />
                    )}
                    恢复执行
                  </Button>
                )}
                {!collapsed && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 text-xs"
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelectedTaskId(taskId);
                    }}
                  >
                    查看详情
                  </Button>
                )}
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4",
                    !collapsed ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
        </div>
        <ChainOfThoughtContent className="px-4 pb-4">
          {task.prompt && (
            <ChainOfThoughtStep
              label={
                <Streamdown
                  {...streamdownPluginsWithWordAnimation}
                  components={{ a: CitationLink }}
                >
                  {task.prompt}
                </Streamdown>
              }
            ></ChainOfThoughtStep>
          )}
          {task.status === "in_progress" &&
            task.latestMessage &&
            hasToolCalls(task.latestMessage) && (
              <ChainOfThoughtStep
                label={t.subtasks.in_progress}
                icon={<Loader2Icon className="size-4 animate-spin" />}
              >
                {explainLastToolCall(task.latestMessage, t)}
              </ChainOfThoughtStep>
            )}
          {task.status === "completed" && (
            <>
              <ChainOfThoughtStep
                label={t.subtasks.completed}
                icon={<CheckCircleIcon className="size-4" />}
              ></ChainOfThoughtStep>
              <ChainOfThoughtStep
                label={
                  task.result ? (
                    <MarkdownContent
                      content={task.result}
                      isLoading={false}
                      rehypePlugins={rehypePlugins}
                    />
                  ) : null
                }
              ></ChainOfThoughtStep>
            </>
          )}
          {task.status === "failed" && (
            <ChainOfThoughtStep
              label={<div className="text-red-500">{task.error}</div>}
              icon={<XCircleIcon className="size-4 text-red-500" />}
            ></ChainOfThoughtStep>
          )}
        </ChainOfThoughtContent>
      </div>
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>停止子任务</AlertDialogTitle>
            <AlertDialogDescription>
              确定要停止子任务「{task.description}」吗？正在执行的工作将丢失。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={() => void handleCancel()}
            >
              确认停止
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </ChainOfThought>
  );
}
