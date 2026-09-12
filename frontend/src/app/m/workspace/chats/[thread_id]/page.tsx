"use client";

import { ArrowDownIcon } from "lucide-react";
import { useEffect, useRef } from "react";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { SidebarProvider } from "@/components/ui/sidebar";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { useChatPage } from "@/components/workspace/chats";
import {
  MessageList,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
} from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { MobileChatHeader } from "@/components/workspace/mobile/chat-header";
import { MobileComposer } from "@/components/workspace/mobile/composer";
import { useScrollToBottom } from "@/components/workspace/mobile/use-scroll-to-bottom";
import { useI18n } from "@/core/i18n/hooks";
import { SubtasksProvider } from "@/core/tasks/context";

import "@/components/workspace/mobile/chat-surface.css";

/**
 * Mobile chat page (prototype ②).
 *
 * Behaviour is entirely `useChatPage()` — the hook both chat pages share — and
 * this file only arranges the transcript, the header and the composer. It is
 * called with no `basePath` on purpose: the page is served through the
 * middleware rewrite, so its public routes are still `/workspace/chats/...`
 * (plan §1.2.1) and the address bar must never show `/m/`.
 *
 * `autoFocus` is deliberately absent from the composer: on a phone the
 * keyboard would cover half the transcript the moment the page opens.
 */
export default function MobileChatPage() {
  // The providers have to sit *above* `useChatPage()`: the hook calls
  // `useSpecificChatMode()`, which reads the prompt-input controller.
  return (
    <SubtasksProvider>
      <SidebarProvider className="h-full min-h-0 flex-col">
        <ArtifactsProvider>
          <PromptInputProvider>
            <MobileChatScreen />
          </PromptInputProvider>
        </ArtifactsProvider>
      </SidebarProvider>
    </SubtasksProvider>
  );
}

/**
 * The desktop tree mounts the same three providers in
 * `app/workspace/chats/[thread_id]/providers.tsx`:
 *
 * - `SubtasksProvider` — the task context `MessageList`'s subtask cards read;
 * - `ArtifactsProvider` — the artifact context the transcript's present-files
 *   group resolves through. It closes the desktop sidebar when an artifact is
 *   selected, so it calls `useSidebar()` and needs a `SidebarProvider` above it
 *   even though the mobile tree has no sidebar; the wrapper is reduced to a
 *   plain full-height container (the default is `min-h-svh`, which would add an
 *   outer scrollbar inside the mobile shell).
 * - `PromptInputProvider` — the composer state; `useSpecificChatMode()` reads
 *   its controller, so the page cannot mount without it.
 */
function MobileChatScreen() {
  const { t } = useI18n();
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const {
    threadId,
    isMock,
    isNewThread,
    thread,
    title,
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
    isDemoMode,
    isInputDisabled,
    canRegenerate,
  } = useChatPage();
  const { isAtBottom, scrollToBottom } = useScrollToBottom(
    surfaceRef,
    threadId,
  );

  // `ThreadTitle` owns this on the desktop, but it renders nothing until the
  // backend reports a title — which would leave the mobile header empty for a
  // brand-new chat.
  useEffect(() => {
    const current = title || (isNewThread ? t.pages.newChat : t.pages.untitled);
    document.title = `${current} - ${t.pages.appName}`;
  }, [isNewThread, t.pages.appName, t.pages.newChat, t.pages.untitled, title]);

  return (
    <ThreadContext.Provider value={{ thread, isMock }}>
      <div className="mobile-chat-surface flex h-full min-h-0 flex-col">
        <MobileChatHeader
          title={title}
          threadId={threadId}
          thread={thread}
          isNewThread={isNewThread}
          isMock={isMock}
          isBusy={thread.isLoading}
          pendingUsageMessages={pendingUsageMessages}
          backendTokenUsage={backendTokenUsage}
          tokenUsageEnabled={tokenUsageEnabled}
          tokenUsagePreferences={localSettings.tokenUsage}
          onTokenUsagePreferencesChange={(preferences) =>
            setLocalSettings("tokenUsage", preferences)
          }
        />

        {/* The transcript owns the remaining height and scrolls internally;
            the composer never scrolls out of the viewport. */}
        <div ref={surfaceRef} className="relative min-h-0 flex-1">
          <MessageList
            className="size-full"
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
          {/* `MessageList` mounts the stick-to-bottom `Conversation` itself and
              takes no children, so the shared `ConversationScrollButton`
              cannot be rendered from here — see `use-scroll-to-bottom.ts`. */}
          {!isAtBottom && (
            <button
              type="button"
              aria-label={t.chats.scrollToBottom}
              data-testid="mobile-chat-scroll-bottom"
              onClick={scrollToBottom}
              className="bg-background absolute bottom-3 left-1/2 z-20 flex size-11 -translate-x-1/2 items-center justify-center rounded-full border shadow-md"
            >
              <ArrowDownIcon aria-hidden="true" className="size-5" />
            </button>
          )}
        </div>

        {/* Rendered directly rather than behind `mountedRef` (the desktop's
            hydration gate): the composer has no client-only first paint, and
            the flag flips without re-rendering, so gating on it could leave
            the input missing until some unrelated state change arrived. */}
        <MobileComposer
          status={
            thread.error ? "error" : thread.isLoading ? "streaming" : "ready"
          }
          context={settings.context}
          disabled={isInputDisabled}
          disabledPlaceholder={
            hasOpenHumanInputCard ? t.humanInput.inputDisabledHint : undefined
          }
          onContextChange={(context) => setSettings("context", context)}
          onSubmit={handleSubmit}
          onStop={handleStop}
        />
      </div>
    </ThreadContext.Provider>
  );
}
