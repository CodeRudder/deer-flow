"use client";

import { useCallback, useEffect, useState } from "react";

import { type PromptInputMessage } from "@/components/ai-elements/prompt-input";
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
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import {
  ChatBox,
  useSpecificChatMode,
  useThreadChat,
} from "@/components/workspace/chats";
import { ExportTrigger } from "@/components/workspace/export-trigger";
import { InputBox } from "@/components/workspace/input-box";
import {
  MessageList,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
  MESSAGE_LIST_FOLLOWUPS_EXTRA_PADDING_BOTTOM,
} from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { BackgroundCommandsIndicator } from "@/components/workspace/background-commands-indicator";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { Welcome } from "@/components/workspace/welcome";
import { useI18n } from "@/core/i18n/hooks";
import { getBackendBaseURL } from "@/core/config";
import { useNotification } from "@/core/notification/hooks";
import { useThreadSettings } from "@/core/settings";
import { useThreadStream } from "@/core/threads/hooks";
import { textOfMessage } from "@/core/threads/utils";
import { useSubtasks, useUpdateSubtask } from "@/core/tasks/context";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export default function ChatPage() {
  const { t } = useI18n();
  const [showFollowups, setShowFollowups] = useState(false);
  const { threadId, setThreadId, isNewThread, setIsNewThread, isMock } =
    useThreadChat();
  const [settings, setSettings] = useThreadSettings(threadId);
  const [mounted, setMounted] = useState(false);
  useSpecificChatMode();

  useEffect(() => {
    setMounted(true);
  }, []);

  const { showNotification } = useNotification();

  const [thread, sendMessage, isUploading] = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    context: settings.context,
    isMock,
    onStart: (createdThreadId) => {
      setThreadId(createdThreadId);
      setIsNewThread(false);
      // ! Important: Never use next.js router for navigation in this case, otherwise it will cause the thread to re-mount and lose all states. Use native history API instead.
      history.replaceState(null, "", `/workspace/chats/${createdThreadId}`);
    },
    onFinish: (state) => {
      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages.at(-1);
        if (lastMessage) {
          const textContent = textOfMessage(lastMessage);
          if (textContent) {
            body =
              textContent.length > 200
                ? textContent.substring(0, 200) + "..."
                : textContent;
          }
        }
        showNotification(state.title, { body });
      }
    },
  });

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      void sendMessage(threadId, message);
    },
    [sendMessage, threadId],
  );
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [stopTargets, setStopTargets] = useState<{
    mainSession: boolean;
    subtaskIds: Record<string, boolean>;
  }>({ mainSession: true, subtaskIds: {} });
  const subtasks = useSubtasks();
  const runningSubtasks = subtasks.filter((s) => s.status === "in_progress");
  const updateSubtask = useUpdateSubtask();

  const handleStopRequest = useCallback(() => {
    // Default: stop main session, but not subtasks
    setStopTargets({
      mainSession: true,
      subtaskIds: {},
    });
    setStopConfirmOpen(true);
  }, []);

  const handleStop = useCallback(async () => {
    const promises: Promise<void>[] = [];

    if (stopTargets.mainSession) {
      promises.push(
        thread.stop().then(async () => {
          // Also cancel the server-side run so LLM stops executing
          try {
            await fetch(`${getBackendBaseURL()}/api/runs/cancel-all`, {
              method: "POST",
            });
          } catch {
            // Best-effort: client-side stop already succeeded
          }
        }),
      );
    }

    // Cancel selected subtasks
    for (const [taskId, checked] of Object.entries(
      stopTargets.subtaskIds,
    )) {
      if (checked) {
        promises.push(
          (async () => {
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
            }
          })(),
        );
      }
    }

    await Promise.all(promises);
    setStopConfirmOpen(false);
  }, [thread, stopTargets, updateSubtask]);

  const messageListPaddingBottom = showFollowups
    ? MESSAGE_LIST_DEFAULT_PADDING_BOTTOM +
      MESSAGE_LIST_FOLLOWUPS_EXTRA_PADDING_BOTTOM
    : undefined;

  return (
    <ThreadContext.Provider value={{ thread, isMock }}>
      <ChatBox threadId={threadId}>
        <div className="relative flex size-full min-h-0 justify-between">
          <header
            className={cn(
              "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center px-4",
              isNewThread
                ? "bg-background/0 backdrop-blur-none"
                : "bg-background/80 shadow-xs backdrop-blur",
            )}
          >
            <div className="flex w-full items-center text-sm font-medium">
              <ThreadTitle threadId={threadId} thread={thread} />
            </div>
            <div className="flex items-center gap-2">
              <TokenUsageIndicator messages={thread.messages} />
              <ExportTrigger threadId={threadId} />
              <ArtifactTrigger />
            </div>
          </header>
          <main className="flex min-h-0 max-w-full grow flex-col">
            <div className="flex size-full justify-center">
              <MessageList
                className={cn("size-full", !isNewThread && "pt-10")}
                threadId={threadId}
                thread={thread}
                paddingBottom={messageListPaddingBottom}
              />
            </div>
            <div className="absolute right-0 bottom-0 left-0 z-30 flex justify-center px-4">
              <div
                className={cn(
                  "relative w-full",
                  isNewThread && "-translate-y-[calc(50vh-96px)]",
                  isNewThread
                    ? "max-w-(--container-width-sm)"
                    : "max-w-(--container-width-md)",
                )}
              >
                <div className="absolute -top-4 right-0 left-0 z-0">
                  <div className="absolute right-0 bottom-0 left-0">
                    <TodoList
                      className="bg-background/5"
                      todos={thread.values.todos ?? []}
                      hidden={
                        !thread.values.todos || thread.values.todos.length === 0
                      }
                    />
                  </div>
                </div>
                {mounted ? (
                  <InputBox
                    className={cn("bg-background/5 w-full -translate-y-4")}
                    isNewThread={isNewThread}
                    threadId={threadId}
                    autoFocus={isNewThread}
                    status={
                      thread.error
                        ? "error"
                        : thread.isLoading
                          ? "streaming"
                          : "ready"
                    }
                    context={settings.context}
                    extraHeader={
                      isNewThread && <Welcome mode={settings.context.mode} />
                    }
                    disabled={
                      env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ||
                      isUploading
                    }
                    onContextChange={(context) =>
                      setSettings("context", context)
                    }
                    onFollowupsVisibilityChange={setShowFollowups}
                    onSubmit={handleSubmit}
                    onStop={handleStopRequest}
                  />
                ) : (
                  <div
                    aria-hidden="true"
                    className={cn(
                      "bg-background/5 h-32 w-full -translate-y-4 rounded-2xl border",
                    )}
                  />
                )}
                {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" && (
                  <div className="text-muted-foreground/67 w-full translate-y-12 text-center text-xs">
                    {t.common.notAvailableInDemoMode}
                  </div>
                )}
              </div>
            </div>
          </main>
        </div>
      </ChatBox>
      {threadId && <BackgroundCommandsIndicator threadId={threadId} />}
      <AlertDialog open={stopConfirmOpen} onOpenChange={setStopConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>停止会话</AlertDialogTitle>
            <AlertDialogDescription>
              选择要停止的目标：
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex max-h-60 flex-col gap-3 overflow-y-auto py-2">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={stopTargets.mainSession}
                onChange={(e) =>
                  setStopTargets((prev) => ({
                    ...prev,
                    mainSession: e.target.checked,
                  }))
                }
                className="size-4 rounded border-gray-300"
              />
              <span className="font-medium">主会话</span>
            </label>
            {runningSubtasks.length > 0 && (
              <>
                <div className="text-muted-foreground text-xs">
                  运行中的子任务
                  <button
                    type="button"
                    className="text-primary ml-2 underline"
                    onClick={() => {
                      const allChecked = runningSubtasks.every(
                        (s) => stopTargets.subtaskIds[s.id],
                      );
                      const newIds: Record<string, boolean> = {};
                      for (const s of runningSubtasks) {
                        newIds[s.id] = !allChecked;
                      }
                      setStopTargets((prev) => ({
                        ...prev,
                        subtaskIds: { ...prev.subtaskIds, ...newIds },
                      }));
                    }}
                  >
                    {runningSubtasks.every((s) => stopTargets.subtaskIds[s.id])
                      ? "取消全选"
                      : "全选"}
                  </button>
                </div>
                {runningSubtasks.map((s) => (
                  <label
                    key={s.id}
                    className="flex items-center gap-2 text-sm pl-4"
                  >
                    <input
                      type="checkbox"
                      checked={!!stopTargets.subtaskIds[s.id]}
                      onChange={(e) =>
                        setStopTargets((prev) => ({
                          ...prev,
                          subtaskIds: {
                            ...prev.subtaskIds,
                            [s.id]: e.target.checked,
                          },
                        }))
                      }
                      className="size-4 rounded border-gray-300"
                    />
                    <span>{s.description || s.id}</span>
                  </label>
                ))}
              </>
            )}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={() => void handleStop()}
              disabled={
                !stopTargets.mainSession &&
                !Object.values(stopTargets.subtaskIds).some(Boolean)
              }
            >
              确认停止
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </ThreadContext.Provider>
  );
}
