"use client";

import { ArrowDownIcon } from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef } from "react";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { SidebarProvider } from "@/components/ui/sidebar";
import {
  ArtifactsProvider,
  useArtifacts,
} from "@/components/workspace/artifacts";
import { useChatPage } from "@/components/workspace/chats";
import {
  MessageList,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
} from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { artifactHref } from "@/components/workspace/mobile/artifact-navigation";
import { MobileChatHeader } from "@/components/workspace/mobile/chat-header";
import { MobileComposer } from "@/components/workspace/mobile/composer";
import { useScrollToBottom } from "@/components/workspace/mobile/use-scroll-to-bottom";
import { TodoList } from "@/components/workspace/todo-list";
import { useAgent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { SubtasksProvider } from "@/core/tasks/context";

import "@/components/workspace/mobile/chat-surface.css";

/**
 * Chat with one custom agent (G2, prototype ②) — the mobile half of
 * `app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`.
 *
 * The desktop's agent page is the one chat page that does not call
 * `useChatPage()`: it inlines the whole behaviour layer (and writes
 * `agent_name` into three separate calls by hand). This page does the opposite
 * — `useChatPage({ agentName })` owns the behaviour, including those three
 * `agent_name` injections and the `/workspace/agents/<name>/chats/…` routes —
 * so the file is a layout: transcript, header, composer.
 *
 * The shell below (providers, artifact routing, scroll-to-bottom) is the mobile
 * chat page's, unchanged and on purpose: an agent thread writes files and
 * raises clarifications exactly like a plain one. The one difference is where
 * "this chat" is: the base path is derived from `agentName`, so the new-thread
 * `history.replaceState` and the gone-thread redirect both land under
 * `/workspace/agents/<name>/` — public paths, so the address bar never shows
 * `/m/` (plan §1.2.1).
 *
 * The header shows the *agent's* name, not the thread title: on this screen the
 * reader chose the agent and the thread title is noise (the desktop shows the
 * name in its badge too). Back goes to `/agents`, the mobile gallery the card
 * that opened this screen lives on — not `/workspace`.
 */
export default function MobileAgentChatPage() {
  const params = useParams<{ agent_name: string; thread_id: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const agentName = params.agent_name;
  const threadId = params.thread_id;
  const isMock = searchParams.get("mock") === "true";

  /**
   * Artifacts are a route on a phone, not a panel — the same wiring, for the
   * same reason, as the mobile chat page (T6): the transcript's `write_file`
   * steps ask the artifacts context to "open" a selection, and there is nothing
   * here to open. `select()` and `setOpen(true)` arrive as a pair, so the second
   * call is deduped against the first; one tap must not push twice.
   */
  const pushedRef = useRef<string | null>(null);
  const selectedRef = useRef<string | null>(null);
  const openArtifact = useCallback(
    (artifact: string) => {
      // A brand-new chat has no thread to read the artifact from, and nothing
      // can have selected an artifact in one.
      if (!threadId || threadId === "new") {
        return;
      }
      const href = artifactHref(threadId, artifact, { isMock });
      if (pushedRef.current === href) {
        return;
      }
      pushedRef.current = href;
      router.push(href);
    },
    [isMock, router, threadId],
  );
  const handleArtifactSelect = useCallback(
    (artifact: string) => {
      selectedRef.current = artifact;
      openArtifact(artifact);
    },
    [openArtifact],
  );
  const handleArtifactsOpenChange = useCallback(
    (open: boolean) => {
      const artifact = selectedRef.current;
      if (open && artifact) {
        openArtifact(artifact);
      }
    },
    [openArtifact],
  );

  // The providers have to sit *above* `useChatPage()`: the hook calls
  // `useSpecificChatMode()`, which reads the prompt-input controller.
  return (
    <SubtasksProvider>
      <SidebarProvider className="h-full min-h-0 flex-col">
        <ArtifactsProvider
          onSelect={handleArtifactSelect}
          onOpenChange={handleArtifactsOpenChange}
        >
          <PromptInputProvider>
            <MobileAgentChatScreen agentName={agentName} />
          </PromptInputProvider>
        </ArtifactsProvider>
      </SidebarProvider>
    </SubtasksProvider>
  );
}

/**
 * Publishes the thread's artifact list to the artifacts context, so the
 * header's 文件 row and the transcript's present-files group have a list to
 * work from. The desktop does this in `ChatBox`, which neither mobile tree
 * renders.
 */
function ThreadArtifacts({ artifacts }: { artifacts: unknown }) {
  const { setArtifacts } = useArtifacts();

  useEffect(() => {
    if (Array.isArray(artifacts)) {
      setArtifacts(artifacts as string[]);
    }
  }, [artifacts, setArtifacts]);

  return null;
}

function MobileAgentChatScreen({ agentName }: { agentName: string }) {
  const { t } = useI18n();
  // The agent's own record, for its display name and nothing else — the chat
  // itself is addressed by `agent_name` (the route param). Same query key as
  // the gallery, so this costs no extra request when the card just loaded it.
  const { agent } = useAgent(agentName);
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const {
    threadId,
    isMock,
    isNewThread,
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
    isDemoMode,
    isInputDisabled,
    canRegenerate,
  } = useChatPage({ agentName });
  const { isAtBottom, scrollToBottom } = useScrollToBottom(
    surfaceRef,
    threadId,
  );

  const agentTitle = agent?.name ?? agentName;

  // `ThreadTitle` owns this on the desktop, and this page does not show the
  // thread title at all — the tab is named after the agent.
  useEffect(() => {
    document.title = `${agentTitle} - ${t.pages.appName}`;
  }, [agentTitle, t.pages.appName]);

  return (
    <ThreadContext.Provider value={{ thread, isMock }}>
      <ThreadArtifacts artifacts={thread.values.artifacts} />
      <div className="mobile-chat-surface flex h-full min-h-0 flex-col">
        <MobileChatHeader
          title={agentTitle}
          backHref="/agents"
          backLabel={t.agents.title}
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
            // C6: a tool-call turn arrives as "ran N steps · M tools" and opens
            // on tap. The desktop's dense panel is the same component with the
            // default, so it is untouched.
            collapsedSteps
            onRegenerateMessage={handleRegenerate}
            onSubmitHumanInput={
              isMock || isDemoMode ? undefined : handleSubmitHumanInput
            }
          />
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

        {/* C11: the plan-mode todo panel, above the input — same source and
            same gate as the mobile chat page (`useChatPage()`'s `hasTodos`),
            and nothing renders when the thread has none. */}
        {hasTodos && (
          <div
            data-testid="mobile-todo-list"
            className="shrink-0 px-2 pt-1.5 pb-1.5"
          >
            <TodoList
              className="mobile-todo-panel bg-background/5"
              todos={thread.values.todos ?? []}
              hidden={false}
              showProgress
              showDurations
            />
          </div>
        )}

        <MobileComposer
          status={
            thread.error ? "error" : thread.isLoading ? "streaming" : "ready"
          }
          threadId={threadId}
          messages={thread.messages}
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
