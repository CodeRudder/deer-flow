import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { ChevronUpIcon, Loader2Icon, RefreshCcwIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  Conversation,
  ConversationContent,
} from "@/components/ai-elements/conversation";
import {
  Reasoning,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import {
  deriveHumanInputThreadState,
  extractHumanInputRequest,
  shouldClearPendingHumanInputOnThreadError,
  type HumanInputRequest,
  type HumanInputResponse,
} from "@/core/messages/human-input";
import {
  buildTokenDebugSteps,
  type TokenUsageInlineMode,
} from "@/core/messages/usage-model";
import {
  extractContentFromMessage,
  extractPresentFilesFromMessage,
  extractTextFromMessage,
  getAssistantTurnCopyData,
  getAssistantTurnUsageMessages,
  getMessageGroups,
  getStreamingMessageLookup,
  hasAssistantTextInCurrentTurn,
  hasContent,
  hasPresentFiles,
  hasReasoning,
  isAssistantMessageGroupStreaming,
  isHiddenFromUIMessage,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { useSubtaskStatuses } from "@/core/subagents/hooks";
import type { Subtask } from "@/core/tasks";
import { useUpdateSubtask } from "@/core/tasks/context";
import {
  derivePendingSubtaskStatus,
  parseSubtaskResult,
} from "@/core/tasks/subtask-result";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { ArtifactFileList } from "../artifacts/artifact-file-list";
import { CopyButton } from "../copy-button";
import { SubtaskDetailSheet } from "../subtask-detail-sheet";
import { Tooltip } from "../tooltip";

import { HumanInputAnsweredRow } from "./human-input-answered-row";
import {
  HumanInputCard,
  type HumanInputSubmitResult,
} from "./human-input-card";
import { MarkdownContent } from "./markdown-content";
import { MessageGroup } from "./message-group";
import { MessageListItem } from "./message-list-item";
import {
  MessageTokenUsageDebugList,
  MessageTokenUsageList,
} from "./message-token-usage";
import { MessageListSkeleton } from "./skeleton";
import { SubtaskCard } from "./subtask-card";

export const MESSAGE_LIST_DEFAULT_PADDING_BOTTOM = 24;

const LOAD_MORE_HISTORY_THROTTLE_MS = 1200;

function mergeSubtaskUpdate(
  updates: Map<string, Partial<Subtask> & { id: string }>,
  update: Partial<Subtask> & { id: string },
) {
  updates.set(update.id, {
    ...updates.get(update.id),
    ...update,
  });
}

function LoadMoreHistoryIndicator({
  isLoading,
  hasMore,
  loadMore,
}: {
  isLoading?: boolean;
  hasMore?: boolean;
  loadMore?: () => void;
}) {
  const { t } = useI18n();
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLoadRef = useRef(0);

  const throttledLoadMore = useCallback(() => {
    if (!hasMore || isLoading) {
      return;
    }

    const now = Date.now();
    const remaining =
      LOAD_MORE_HISTORY_THROTTLE_MS - (now - lastLoadRef.current);

    if (remaining <= 0) {
      lastLoadRef.current = now;
      loadMore?.();
      return;
    }

    if (timeoutRef.current) {
      return;
    }

    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      if (!hasMore || isLoading) {
        return;
      }
      lastLoadRef.current = Date.now();
      loadMore?.();
    }, remaining);
  }, [hasMore, isLoading, loadMore]);

  useEffect(() => {
    const element = sentinelRef.current;
    if (!element || !hasMore) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          throttledLoadMore();
        }
      },
      {
        rootMargin: "120px 0px 0px 0px",
      },
    );

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [hasMore, throttledLoadMore]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  if (!hasMore && !isLoading) {
    return null;
  }

  return (
    <div ref={sentinelRef} className="flex w-full justify-center">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="text-muted-foreground hover:text-foreground rounded-full px-3"
        disabled={(isLoading ?? false) || !hasMore}
        onClick={throttledLoadMore}
      >
        {isLoading ? (
          <>
            <Loader2Icon className="mr-2 size-4 animate-spin" />
            {t.common.loading}
          </>
        ) : (
          <>
            <ChevronUpIcon className="mr-2 size-4" />
            {t.common.loadMore}
          </>
        )}
      </Button>
    </div>
  );
}

export function MessageList({
  className,
  threadId,
  thread,
  paddingBottom = MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
  statusPollingEnabled = true,
  tokenUsageInlineMode = "off",
  hasMoreHistory,
  loadMoreHistory,
  isHistoryLoading,
  onRegenerateMessage,
  canRegenerate = false,
  onSubmitHumanInput,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  paddingBottom?: number;
  statusPollingEnabled?: boolean;
  tokenUsageInlineMode?: TokenUsageInlineMode;
  hasMoreHistory?: boolean;
  loadMoreHistory?: () => void;
  isHistoryLoading?: boolean;
  onRegenerateMessage?: (
    messageId: string,
    supersededMessageIds: string[],
  ) => void | Promise<void>;
  canRegenerate?: boolean;
  onSubmitHumanInput?: (
    request: HumanInputRequest,
    response: HumanInputResponse,
  ) => HumanInputSubmitResult | Promise<HumanInputSubmitResult>;
}) {
  const { t } = useI18n();
  const [turnStartTime, setTurnStartTime] = useState<number | null>(null);
  const prevIsLoading = useRef(thread.isLoading);

  useEffect(() => {
    if (thread.isLoading && !prevIsLoading.current) {
      setTurnStartTime(Date.now());
    }
    prevIsLoading.current = thread.isLoading;
  }, [thread.isLoading]);
  const messages = thread.messages;
  const { data: subtaskStatuses } = useSubtaskStatuses(
    threadId,
    statusPollingEnabled,
    thread.isLoading,
  );
  const updateSubtask = useUpdateSubtask();
  const prevStatusFingerprintRef = useRef("");
  const prevMessageSubtaskFingerprintRef = useRef("");
  const updateSubtaskRef = useRef(updateSubtask);
  updateSubtaskRef.current = updateSubtask;
  const { messageSubtaskUpdates, messageSubtaskFingerprint } = useMemo(() => {
    const updates = new Map<string, Partial<Subtask> & { id: string }>();
    for (const message of messages) {
      if (message.type === "ai") {
        for (const toolCall of message.tool_calls ?? []) {
          if (toolCall.name === "task" && toolCall.id) {
            mergeSubtaskUpdate(updates, {
              id: toolCall.id,
              subagent_type:
                typeof toolCall.args.subagent_type === "string"
                  ? toolCall.args.subagent_type
                  : "",
              description:
                typeof toolCall.args.description === "string"
                  ? toolCall.args.description
                  : "",
              prompt:
                typeof toolCall.args.prompt === "string"
                  ? toolCall.args.prompt
                  : "",
              status: "in_progress",
            });
          }
        }
      } else if (message.type === "tool" && message.tool_call_id) {
        mergeSubtaskUpdate(updates, {
          id: message.tool_call_id,
          ...parseSubtaskResult(extractTextFromMessage(message)),
        });
      }
    }
    const messageSubtaskUpdates = Array.from(updates.values());
    const messageSubtaskFingerprint = JSON.stringify(messageSubtaskUpdates);
    return { messageSubtaskUpdates, messageSubtaskFingerprint };
  }, [messages]);

  useEffect(() => {
    if (!subtaskStatuses) return;

    const fingerprint = subtaskStatuses
      .map((status) => `${status.task_id}:${status.status}`)
      .join(",");
    if (fingerprint === prevStatusFingerprintRef.current) {
      return;
    }
    prevStatusFingerprintRef.current = fingerprint;

    for (const item of subtaskStatuses) {
      const status = item.status as Subtask["status"];
      if (
        status === "completed" ||
        status === "failed" ||
        status === "interrupted"
      ) {
        updateSubtaskRef.current({
          id: item.task_id,
          status,
          subagent_type: item.subagent_name,
          description: item.description,
        });
      }
    }
  }, [subtaskStatuses]);

  useEffect(() => {
    if (
      messageSubtaskFingerprint === prevMessageSubtaskFingerprintRef.current
    ) {
      return;
    }
    prevMessageSubtaskFingerprintRef.current = messageSubtaskFingerprint;

    for (const taskUpdate of messageSubtaskUpdates) {
      updateSubtaskRef.current(taskUpdate);
    }
  }, [messageSubtaskFingerprint, messageSubtaskUpdates]);

  const groupedMessages = getMessageGroups(messages, {
    isCurrentTurnLoading: thread.isLoading,
  });
  const [regeneratingMessageId, setRegeneratingMessageId] = useState<
    string | null
  >(null);
  const hasActiveAssistantText = useMemo(
    () => hasAssistantTextInCurrentTurn(messages, groupedMessages),
    [groupedMessages, messages],
  );
  const rehypePlugins = useRehypeSplitWordsIntoSpans(thread.isLoading);
  const lastGroupIndex = groupedMessages.length - 1;
  const turnUsageMessagesByGroupIndex =
    getAssistantTurnUsageMessages(groupedMessages);
  const tokenDebugSteps = useMemo(
    () => buildTokenDebugSteps(messages, t),
    [messages, t],
  );
  const streamingMessages = useMemo(
    () =>
      getStreamingMessageLookup(
        messages,
        thread.isLoading,
        thread.getMessagesMetadata,
      ),
    [messages, thread.getMessagesMetadata, thread.isLoading],
  );

  const latestAssistantGroupId = useMemo(() => {
    if (thread.isLoading) {
      return null;
    }
    for (let i = groupedMessages.length - 1; i >= 0; i -= 1) {
      const group = groupedMessages[i];
      if (group?.type === "assistant") {
        return group.id;
      }
    }
    return null;
  }, [groupedMessages, thread.isLoading]);

  const [pendingHumanInputRequestIds, setPendingHumanInputRequestIds] =
    useState<ReadonlySet<string>>(() => new Set());
  const previousHumanInputThreadError = useRef<unknown>(thread.error);
  const humanInputState = useMemo(
    () =>
      deriveHumanInputThreadState(
        messages,
        (message) => !isHiddenFromUIMessage(message),
      ),
    [messages],
  );

  useEffect(() => {
    if (pendingHumanInputRequestIds.size === 0) {
      return;
    }
    setPendingHumanInputRequestIds((previous) => {
      const next = new Set(previous);
      for (const requestId of previous) {
        if (humanInputState.answeredResponses.has(requestId)) {
          next.delete(requestId);
        }
      }
      return next.size === previous.size ? previous : next;
    });
  }, [humanInputState.answeredResponses, pendingHumanInputRequestIds.size]);

  useEffect(() => {
    const previousError = previousHumanInputThreadError.current;
    previousHumanInputThreadError.current = thread.error;

    if (
      !shouldClearPendingHumanInputOnThreadError({
        currentError: thread.error,
        pendingRequestCount: pendingHumanInputRequestIds.size,
        previousError,
      })
    ) {
      return;
    }

    // Async stream errors arrive via thread.error after sendMessage resolved;
    // the reply never reached history, so unlock the card for retry.
    setPendingHumanInputRequestIds(new Set());
  }, [pendingHumanInputRequestIds.size, thread.error]);

  const clearPendingHumanInput = useCallback((requestId: string) => {
    setPendingHumanInputRequestIds((previous) => {
      if (!previous.has(requestId)) {
        return previous;
      }
      const next = new Set(previous);
      next.delete(requestId);
      return next;
    });
  }, []);

  const handleSubmitHumanInput = useCallback(
    async (request: HumanInputRequest, response: HumanInputResponse) => {
      setPendingHumanInputRequestIds((previous) => {
        const next = new Set(previous);
        next.add(request.request_id);
        return next;
      });

      try {
        const result = await onSubmitHumanInput?.(request, response);
        if (result === false) {
          clearPendingHumanInput(request.request_id);
        }
        return result;
      } catch (error) {
        clearPendingHumanInput(request.request_id);
        toast.error(error instanceof Error ? error.message : String(error));
        return false;
      }
    },
    [clearPendingHumanInput, onSubmitHumanInput],
  );

  const renderAssistantActions = useCallback(
    (
      messages: Message[],
      isStreaming: boolean,
      enableRegenerateForTurn: boolean,
    ) => {
      const clipboardData = getAssistantTurnCopyData(messages, { isStreaming });
      const regenerateTarget = [...messages]
        .reverse()
        .find((message) => message.type === "ai" && message.id);
      const supersededMessageIds = messages
        .filter((message) => message.type === "ai" && message.id)
        .map((message) => message.id)
        .filter((id): id is string => typeof id === "string");

      if (!clipboardData && !regenerateTarget) {
        return null;
      }

      return (
        <div className="mt-2 flex justify-start gap-1 opacity-0 transition-opacity delay-200 duration-300 group-hover/assistant-turn:opacity-100">
          {clipboardData && <CopyButton clipboardData={clipboardData} />}
          {enableRegenerateForTurn &&
            regenerateTarget?.id &&
            onRegenerateMessage && (
              <Tooltip content={t.common.regenerate}>
                <Button
                  aria-label={t.common.regenerate}
                  size="icon-sm"
                  type="button"
                  variant="ghost"
                  disabled={
                    !canRegenerate ||
                    regeneratingMessageId === regenerateTarget.id
                  }
                  onClick={() => {
                    const targetId = regenerateTarget.id;
                    if (!targetId) {
                      return;
                    }
                    setRegeneratingMessageId(targetId);
                    void Promise.resolve(
                      onRegenerateMessage?.(targetId, supersededMessageIds),
                    ).finally(() => {
                      setRegeneratingMessageId(null);
                    });
                  }}
                >
                  <RefreshCcwIcon
                    className={cn(
                      "size-3",
                      regeneratingMessageId === regenerateTarget.id &&
                        "animate-spin",
                    )}
                  />
                </Button>
              </Tooltip>
            )}
        </div>
      );
    },
    [
      canRegenerate,
      onRegenerateMessage,
      regeneratingMessageId,
      t.common.regenerate,
    ],
  );

  const renderTokenUsage = useCallback(
    ({
      messages,
      turnUsageMessages,
      inlineDebug = true,
      debugMessageIds,
    }: {
      messages: Message[];
      turnUsageMessages?: Message[] | null;
      inlineDebug?: boolean;
      debugMessageIds?: string[];
    }) => {
      if (tokenUsageInlineMode === "per_turn") {
        return (
          <MessageTokenUsageList
            enabled={true}
            isLoading={thread.isLoading}
            messages={turnUsageMessages ?? []}
          />
        );
      }

      if (tokenUsageInlineMode === "step_debug" && inlineDebug) {
        const messageIds = new Set(
          debugMessageIds ??
            messages
              .filter((message) => message.type === "ai")
              .map((message) => message.id)
              .filter((id): id is string => typeof id === "string"),
        );
        return (
          <MessageTokenUsageDebugList
            enabled={true}
            isLoading={thread.isLoading}
            steps={tokenDebugSteps.filter((step) =>
              messageIds.has(step.messageId),
            )}
          />
        );
      }

      return null;
    },
    [thread.isLoading, tokenDebugSteps, tokenUsageInlineMode],
  );

  if (thread.isThreadLoading && messages.length === 0) {
    return <MessageListSkeleton />;
  }

  return (
    <Conversation
      className={cn(
        "flex size-full min-w-0 flex-col justify-center",
        className,
      )}
    >
      <ConversationContent className="mx-auto w-full max-w-(--container-width-md) min-w-0 gap-8 pt-8">
        <LoadMoreHistoryIndicator
          isLoading={isHistoryLoading}
          hasMore={hasMoreHistory}
          loadMore={loadMoreHistory}
        />
        {groupedMessages.map((group, groupIndex) => {
          const turnUsageMessages = turnUsageMessagesByGroupIndex[groupIndex];
          const groupIsLoading =
            thread.isLoading && groupIndex === lastGroupIndex;
          const groupKey = group.id ?? `group-${group.type}-${groupIndex}`;

          if (group.type === "human" || group.type === "assistant") {
            return (
              <div
                key={groupKey}
                className={cn(
                  "w-full",
                  group.type === "assistant" && "group/assistant-turn",
                )}
              >
                {group.messages.map((msg, msgIndex) => {
                  return (
                    <MessageListItem
                      key={`${groupKey}/${msg.id ?? msgIndex}`}
                      message={msg}
                      isLoading={
                        thread.isLoading &&
                        groupIndex === groupedMessages.length - 1
                      }
                      threadId={threadId}
                      showCopyButton={group.type !== "assistant"}
                      turnStartTime={
                        groupIndex === groupedMessages.length - 1
                          ? turnStartTime
                          : null
                      }
                    />
                  );
                })}
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                })}
                {group.type === "assistant" &&
                  renderAssistantActions(
                    group.messages,
                    isAssistantMessageGroupStreaming(
                      group.messages,
                      streamingMessages,
                    ),
                    group.id === latestAssistantGroupId,
                  )}
              </div>
            );
          } else if (group.type === "assistant:clarification") {
            const message = group.messages[0];
            if (!message) {
              return null;
            }

            const humanInputRequest = extractHumanInputRequest(message);
            if (humanInputRequest) {
              const answeredResponse =
                humanInputState.answeredResponses.get(
                  humanInputRequest.request_id,
                ) ?? null;
              const pending = pendingHumanInputRequestIds.has(
                humanInputRequest.request_id,
              );
              return (
                <div key={groupKey} className="w-full">
                  {answeredResponse ? (
                    <HumanInputAnsweredRow
                      request={humanInputRequest}
                      response={answeredResponse}
                    />
                  ) : (
                    <HumanInputCard
                      disabled={
                        thread.isLoading ||
                        pending ||
                        humanInputState.latestOpenRequestId !==
                          humanInputRequest.request_id ||
                        !onSubmitHumanInput
                      }
                      pending={pending}
                      request={humanInputRequest}
                      onSubmit={
                        onSubmitHumanInput
                          ? (response) =>
                              handleSubmitHumanInput(
                                humanInputRequest,
                                response,
                              )
                          : undefined
                      }
                    />
                  )}
                  {renderTokenUsage({
                    messages: group.messages,
                    turnUsageMessages,
                  })}
                </div>
              );
            }

            if (hasContent(message)) {
              return (
                <div key={groupKey} className="w-full">
                  <MarkdownContent
                    content={extractContentFromMessage(message)}
                    isLoading={thread.isLoading}
                    rehypePlugins={rehypePlugins}
                  />
                  {renderTokenUsage({
                    messages: group.messages,
                    turnUsageMessages,
                  })}
                </div>
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
              <div className="w-full" key={groupKey}>
                {group.messages[0] && hasContent(group.messages[0]) && (
                  <MarkdownContent
                    content={extractContentFromMessage(group.messages[0])}
                    isLoading={thread.isLoading}
                    rehypePlugins={rehypePlugins}
                    className="mb-4"
                  />
                )}
                <ArtifactFileList
                  files={files}
                  threadId={threadId}
                  variant="message"
                />
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                })}
              </div>
            );
          } else if (group.type === "assistant:subagent") {
            const tasks = new Set<Subtask>();
            for (const message of group.messages) {
              if (message.type === "ai") {
                for (const toolCall of message.tool_calls ?? []) {
                  if (toolCall.name === "task") {
                    const taskId = toolCall.id;
                    if (!taskId) {
                      continue;
                    }
                    const status = derivePendingSubtaskStatus(
                      taskId,
                      group.messages,
                      groupIsLoading,
                    );
                    const task: Subtask = {
                      id: taskId,
                      subagent_type:
                        typeof toolCall.args.subagent_type === "string"
                          ? toolCall.args.subagent_type
                          : "",
                      description:
                        typeof toolCall.args.description === "string"
                          ? toolCall.args.description
                          : "",
                      prompt:
                        typeof toolCall.args.prompt === "string"
                          ? toolCall.args.prompt
                          : "",
                      status,
                      ...(status === "failed"
                        ? { error: t.subtasks.failed }
                        : {}),
                    };
                    tasks.add(task);
                  }
                }
              }
            }

            const results: React.ReactNode[] = [];
            const subagentDebugMessageIds: string[] = [];
            if (tasks.size > 0) {
              results.push(
                <div
                  key="subtask-count"
                  className="text-muted-foreground pt-2 text-sm font-normal"
                >
                  {t.subtasks.executing(tasks.size)}
                </div>,
              );
            }
            for (const [messageIndex, message] of group.messages
              .filter((message) => message.type === "ai")
              .entries()) {
              if (hasReasoning(message)) {
                results.push(
                  <MessageGroup
                    key={`thinking-group-${message.id ?? `${groupKey}-${messageIndex}`}`}
                    messages={[message]}
                    isLoading={groupIsLoading}
                    tokenDebugSteps={tokenDebugSteps.filter(
                      (step) => step.messageId === message.id,
                    )}
                    showTokenDebugSummaries={
                      tokenUsageInlineMode === "step_debug"
                    }
                  />,
                );
              } else if (message.id) {
                subagentDebugMessageIds.push(message.id);
              }
              const taskIds = message.tool_calls?.flatMap((toolCall) =>
                toolCall.name === "task" && toolCall.id ? [toolCall.id] : [],
              );
              for (const taskId of taskIds ?? []) {
                results.push(
                  <SubtaskCard
                    key={"task-group-" + taskId}
                    taskId={taskId}
                    threadId={threadId}
                    isLoading={groupIsLoading}
                  />,
                );
              }
            }
            return (
              <div
                key={`subtask-group-${groupKey}`}
                className="relative z-1 flex flex-col gap-2"
              >
                {results}
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                  debugMessageIds: subagentDebugMessageIds,
                })}
              </div>
            );
          }
          return (
            <div key={`group-${groupKey}`} className="w-full">
              <MessageGroup
                messages={group.messages}
                isLoading={thread.isLoading}
                tokenDebugSteps={tokenDebugSteps.filter((step) =>
                  group.messages.some(
                    (message) => message.id === step.messageId,
                  ),
                )}
                showTokenDebugSummaries={tokenUsageInlineMode === "step_debug"}
              />
              {renderTokenUsage({
                messages: group.messages,
                turnUsageMessages,
                inlineDebug: false,
              })}
            </div>
          );
        })}
        {(thread.isLoading || pendingHumanInputRequestIds.size > 0) &&
          !hasActiveAssistantText && (
            <div className="w-full">
              <Reasoning isStreaming={true} startTimeProp={turnStartTime}>
                <ReasoningTrigger hasContent={false} />
              </Reasoning>
            </div>
          )}
        <div style={{ height: `${paddingBottom}px` }} />
      </ConversationContent>
      <SubtaskDetailSheet threadId={threadId} />
    </Conversation>
  );
}
