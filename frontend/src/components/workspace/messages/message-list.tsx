import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Conversation,
  ConversationContent,
} from "@/components/ai-elements/conversation";
import { useI18n } from "@/core/i18n/hooks";
import {
  extractContentFromMessage,
  extractPresentFilesFromMessage,
  extractTextFromMessage,
  groupMessages,
  hasContent,
  hasPresentFiles,
  hasReasoning,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { useSubtaskStatuses } from "@/core/subagents/hooks";
import type { Subtask } from "@/core/tasks";
import { useUpdateSubtask } from "@/core/tasks/context";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { ArtifactFileList } from "../artifacts/artifact-file-list";
import { StreamingIndicator } from "../streaming-indicator";

import { MarkdownContent } from "./markdown-content";
import { MessageGroup } from "./message-group";
import { MessageListItem } from "./message-list-item";
import { MessageListSkeleton } from "./skeleton";
import { SubtaskCard } from "./subtask-card";
import { SubtaskDetailSheet } from "../subtask-detail-sheet";

export const MESSAGE_LIST_DEFAULT_PADDING_BOTTOM = 160;
export const MESSAGE_LIST_FOLLOWUPS_EXTRA_PADDING_BOTTOM = 80;

const INITIAL_RENDER_COUNT = 30;
const LOAD_MORE_COUNT = 20;

export function MessageList({
  className,
  threadId,
  thread,
  paddingBottom = MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  paddingBottom?: number;
}) {
  const { t } = useI18n();
  const rehypePlugins = useRehypeSplitWordsIntoSpans(thread.isLoading);
  const updateSubtask = useUpdateSubtask();
  const messages = thread.messages;

  // Sync real subtask statuses from backend API (polls every 10s)
  const { data: subtaskStatuses } = useSubtaskStatuses(threadId);
  const prevStatusFingerprintRef = useRef<string>("");
  const updateSubtaskRef = useRef(updateSubtask);
  updateSubtaskRef.current = updateSubtask;
  useEffect(() => {
    if (!subtaskStatuses) return;
    // Fingerprint to skip redundant updates
    const fp = subtaskStatuses.map((s) => `${s.task_id}:${s.status}`).join(",");
    if (fp === prevStatusFingerprintRef.current) return;
    prevStatusFingerprintRef.current = fp;
    for (const s of subtaskStatuses) {
      const status = s.status as Subtask["status"];
      if (status === "completed" || status === "failed" || status === "interrupted") {
        updateSubtaskRef.current({
          id: s.task_id,
          status,
          subagent_type: s.subagent_name,
          description: s.description,
        });
      }
    }
  }, [subtaskStatuses]);

  // Sync subtask statuses from messages into context — runs in useEffect
  // to avoid calling setState during render (which causes cascading re-renders).
  const prevMsgIdsRef = useRef<string>("");

  useEffect(() => {
    // Build a quick fingerprint of message ids + types to skip redundant work
    const fingerprint = messages
      .map((m) => `${m.id}:${m.type}`)
      .join(",");
    if (fingerprint === prevMsgIdsRef.current) return;
    prevMsgIdsRef.current = fingerprint;

    // Collect task tool_call_ids that have received ToolMessage responses
    const respondedTaskIds = new Set<string>();
    for (const message of messages) {
      if (message.type === "tool" && message.tool_call_id) {
        respondedTaskIds.add(message.tool_call_id);
      }
    }

    for (const message of messages) {
      if (message.type === "ai") {
        for (const toolCall of message.tool_calls ?? []) {
          if (toolCall.name === "task") {
            // During streaming, mark tasks as in_progress so the card shows
            // a running indicator. Final status comes from the tool response.
            updateSubtask({
              id: toolCall.id!,
              subagent_type: toolCall.args.subagent_type,
              description: toolCall.args.description,
              prompt: toolCall.args.prompt,
              ...(thread.isLoading ? { status: "in_progress" as const } : {}),
            });
          }
        }
      } else if (message.type === "tool") {
        const taskId = message.tool_call_id;
        if (taskId) {
          const result = extractTextFromMessage(message);
          if (result.startsWith("Task Succeeded. Result:")) {
            updateSubtask({
              id: taskId,
              status: "completed",
              result: result.split("Task Succeeded. Result:")[1]?.trim(),
            });
          } else if (result.startsWith("Task failed.")) {
            updateSubtask({
              id: taskId,
              status: "failed",
              error: result.split("Task failed.")[1]?.trim(),
            });
          } else if (result.startsWith("Task timed out")) {
            updateSubtask({
              id: taskId,
              status: "failed",
              error: result,
            });
          }
          // Do NOT guess status for unknown response patterns.
          // The task status should only be set when we have a clear signal.
        }
      }
    }
  }, [messages, thread.isLoading, updateSubtask]);

  // Pagination state — must be before any early return (Rules of Hooks)
  const [renderCount, setRenderCount] = useState(INITIAL_RENDER_COUNT);
  const scrollRef = useRef<HTMLDivElement>(null);
  const threadIdRef = useRef(threadId);
  if (threadIdRef.current !== threadId) {
    threadIdRef.current = threadId;
    setRenderCount(INITIAL_RENDER_COUNT);
  }
  const loadMore = useCallback(() => {
    setRenderCount((prev) => prev + LOAD_MORE_COUNT);
  }, []);
  const loadMoreRef = useRef(loadMore);
  loadMoreRef.current = loadMore;
  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      const target = e.currentTarget;
      if (target.scrollTop < 100) {
        loadMoreRef.current();
      }
    },
    [],
  );

  if (thread.isThreadLoading && messages.length === 0) {
    return <MessageListSkeleton />;
  }

  // Group messages once, then paginate the groups
  const allGroups = groupMessages(messages, (group) => group);

  // Show the last N groups (most recent messages)
  const startIndex = Math.max(0, allGroups.length - renderCount);
  const visibleGroups = allGroups.slice(startIndex);
  const hasMore = startIndex > 0;

  return (
    <Conversation
      className={cn("flex size-full flex-col justify-center", className)}
      onScroll={handleScroll}
    >
      <ConversationContent className="mx-auto w-full max-w-(--container-width-md) gap-8 pt-12">
        {hasMore && (
          <button
            className="text-muted-foreground mx-auto block py-2 text-sm hover:underline"
            onClick={loadMore}
          >
            加载更多消息...
          </button>
        )}
        {visibleGroups.map((group) => {
          if (group.type === "human" || group.type === "assistant") {
            return group.messages.map((msg) => {
              // Prefer backend-stamped timestamp from response_metadata
              const rmCreatedAt = msg.response_metadata?.created_at;
              const metaCreatedAt = thread.getMessagesMetadata(msg)?.firstSeenState?.created_at;
              const ts = rmCreatedAt ?? metaCreatedAt;
              return (
                <MessageListItem
                  key={`${group.id}/${msg.id}`}
                  message={msg}
                  isLoading={thread.isLoading}
                  timestamp={ts ? new Date(typeof ts === "number" ? ts * 1000 : ts) : undefined}
                />
              );
            });
          } else if (group.type === "assistant:clarification") {
            const message = group.messages[0];
            if (message && hasContent(message)) {
              return (
                <MarkdownContent
                  key={group.id}
                  content={extractContentFromMessage(message)}
                  isLoading={thread.isLoading}
                  rehypePlugins={rehypePlugins}
                />
              );
            }
            return null;
          } else if (group.type === "assistant:present-files") {
            const files: string[] = [];
            for (const message of group.messages) {
              if (hasPresentFiles(message)) {
                const presentFiles = extractPresentFilesFromMessage(message);
                files.push(...presentFiles);
              }
            }
            return (
              <div className="w-full" key={group.id}>
                {group.messages[0] && hasContent(group.messages[0]) && (
                  <MarkdownContent
                    content={extractContentFromMessage(group.messages[0])}
                    isLoading={thread.isLoading}
                    rehypePlugins={rehypePlugins}
                    className="mb-4"
                  />
                )}
                <ArtifactFileList files={files} threadId={threadId} />
              </div>
            );
          } else if (group.type === "assistant:subagent") {
            const results: React.ReactNode[] = [];
            for (const message of group.messages.filter(
              (message) => message.type === "ai",
            )) {
              if (hasReasoning(message)) {
                results.push(
                  <MessageGroup
                    key={"thinking-group-" + message.id}
                    messages={[message]}
                    isLoading={thread.isLoading}
                    getMessagesMetadata={thread.getMessagesMetadata}
                  />,
                );
              }
              const taskIds = message.tool_calls
                ?.filter((toolCall) => toolCall.name === "task")
                .map((toolCall) => toolCall.id);
              const taskCount = taskIds?.length ?? 0;
              results.push(
                <div
                  key="subtask-count"
                  className="text-muted-foreground pt-2 text-sm font-normal"
                >
                  {t.subtasks.executing(taskCount)}
                </div>,
              );
              for (const taskId of taskIds ?? []) {
                results.push(
                  <SubtaskCard
                    key={"task-group-" + taskId}
                    taskId={taskId!}
                    threadId={threadId}
                    isLoading={thread.isLoading}
                  />,
                );
              }
            }
            return (
              <div
                key={"subtask-group-" + group.id}
                className="relative z-1 flex flex-col gap-2"
              >
                {results}
              </div>
            );
          }
          return (
            <MessageGroup
              key={"group-" + group.id}
              messages={group.messages}
              isLoading={thread.isLoading}
              getMessagesMetadata={thread.getMessagesMetadata}
            />
          );
        })}
        {thread.isLoading && <StreamingIndicator className="my-4" />}
        <div style={{ height: `${paddingBottom}px` }} />
      </ConversationContent>
      <SubtaskDetailSheet threadId={threadId} />
    </Conversation>
  );
}