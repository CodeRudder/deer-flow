// NOTE: Keep feature parity with src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx —
// new header indicators / message-list features must be added to both pages.
"use client";

import { SidebarTrigger } from "@/components/ui/sidebar";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import { ChatBox, useChatPage } from "@/components/workspace/chats";
import { ExportTrigger } from "@/components/workspace/export-trigger";
import { InputBox } from "@/components/workspace/input-box";
import {
  MessageList,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
} from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { QuotaIndicator } from "@/components/workspace/quota-indicator";
import { SessionStatusButton } from "@/components/workspace/session-status-dialog";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { Welcome } from "@/components/workspace/welcome";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export default function ChatPage() {
  const { t } = useI18n();
  // All streaming state, settings, effects and composer callbacks live in the
  // shared hook so the mobile chat page can reuse them verbatim — only the
  // layout below is desktop-specific. See `use-chat-page.ts`.
  const {
    threadId,
    isMock,
    isNewThread,
    isWelcomeMode,
    thread,
    pendingUsageMessages,
    backendTokenUsage,
    tokenUsageEnabled,
    tokenUsageInlineMode,
    settings,
    setSettings,
    localSettings,
    setLocalSettings,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
    hasOpenHumanInputCard,
    handleSubmitHumanInput,
    handleSubmit,
    handleStop,
    handleRegenerate,
    hasTodos,
    mountedRef,
    isDemoMode,
    isInputDisabled,
    canRegenerate,
  } = useChatPage();

  return (
    <ThreadContext.Provider value={{ thread, isMock }}>
      <ChatBox threadId={threadId}>
        <div className="relative flex size-full min-h-0 justify-between">
          <header
            className={cn(
              "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center gap-2 px-2 sm:px-4",
              isWelcomeMode
                ? "bg-background/0 backdrop-blur-none"
                : "bg-background/80 shadow-xs backdrop-blur",
            )}
          >
            <SidebarTrigger className="md:hidden" />
            <div className="flex min-w-0 flex-1 items-center text-sm font-medium">
              <ThreadTitle threadId={threadId} thread={thread} />
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {!isNewThread && !isMock && (
                <SessionStatusButton
                  threadId={threadId}
                  enabled
                  forcePolling={thread.isLoading}
                />
              )}
              <TokenUsageIndicator
                threadId={isNewThread ? undefined : threadId}
                backendUsage={backendTokenUsage}
                enabled={tokenUsageEnabled}
                messages={thread.messages}
                pendingMessages={pendingUsageMessages}
                preferences={localSettings.tokenUsage}
                onPreferencesChange={(preferences) =>
                  setLocalSettings("tokenUsage", preferences)
                }
              />
              {!isMock && <QuotaIndicator isThreadBusy={thread.isLoading} />}
              <ExportTrigger threadId={threadId} />
              <ArtifactTrigger />
            </div>
          </header>
          <main className="flex min-h-0 max-w-full grow flex-col">
            <div className="flex min-h-0 flex-1 justify-center">
              <MessageList
                className={cn("size-full", !isWelcomeMode && "pt-10")}
                threadId={threadId}
                thread={thread}
                paddingBottom={MESSAGE_LIST_DEFAULT_PADDING_BOTTOM}
                hasMoreHistory={hasMoreHistory}
                loadMoreHistory={loadMoreHistory}
                isHistoryLoading={isHistoryLoading}
                tokenUsageInlineMode={tokenUsageInlineMode}
                canRegenerate={canRegenerate}
                onRegenerateMessage={handleRegenerate}
                onSubmitHumanInput={
                  isMock || isDemoMode ? undefined : handleSubmitHumanInput
                }
              />
            </div>
            <div
              className={cn(
                "right-0 bottom-0 left-0 z-30 flex justify-center px-3 sm:px-4",
                isWelcomeMode ? "absolute" : "relative shrink-0 pb-4",
              )}
            >
              <div
                className={cn(
                  "relative w-full",
                  isWelcomeMode &&
                    "-translate-y-[calc(50vh-48px)] sm:-translate-y-[calc(50vh-96px)]",
                  isWelcomeMode
                    ? "max-w-(--container-width-sm)"
                    : "max-w-(--container-width-md)",
                )}
              >
                {hasTodos && (
                  <div
                    className={cn(
                      "right-0 left-0 z-0",
                      isWelcomeMode ? "absolute -top-4" : "relative",
                    )}
                  >
                    <div
                      className={cn(
                        "right-0 bottom-0 left-0",
                        isWelcomeMode ? "absolute" : "relative",
                      )}
                    >
                      <TodoList
                        className="bg-background/5"
                        todos={thread.values.todos ?? []}
                        hidden={false}
                        showProgress
                      />
                    </div>
                  </div>
                )}
                {mountedRef.current ? (
                  <InputBox
                    className={cn(
                      "bg-background/5 w-full",
                      isWelcomeMode && "-translate-y-2 sm:-translate-y-4",
                    )}
                    isWelcomeMode={isWelcomeMode}
                    threadId={threadId}
                    autoFocus={isWelcomeMode}
                    status={
                      thread.error
                        ? "error"
                        : thread.isLoading
                          ? "streaming"
                          : "ready"
                    }
                    context={settings.context}
                    extraHeader={
                      isWelcomeMode && <Welcome mode={settings.context.mode} />
                    }
                    disabled={isInputDisabled}
                    disabledPlaceholder={
                      hasOpenHumanInputCard
                        ? t.humanInput.inputDisabledHint
                        : undefined
                    }
                    onContextChange={(context) =>
                      setSettings("context", context)
                    }
                    onSubmit={handleSubmit}
                    onStop={handleStop}
                  />
                ) : (
                  <div
                    aria-hidden="true"
                    className={cn(
                      "bg-background/5 h-32 w-full rounded-2xl",
                      isWelcomeMode && "-translate-y-2 sm:-translate-y-4",
                    )}
                  />
                )}
                {isDemoMode && (
                  <div className="text-muted-foreground/67 w-full translate-y-12 text-center text-xs">
                    {t.common.notAvailableInDemoMode}
                  </div>
                )}
              </div>
            </div>
          </main>
        </div>
      </ChatBox>
    </ThreadContext.Provider>
  );
}
