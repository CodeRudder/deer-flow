"use client";

import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";

import {
  usePromptInputController,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import {
  type HumanInputRequest,
  type HumanInputResponse,
} from "@/core/messages/human-input";
import type { TokenUsageInlineMode } from "@/core/messages/usage-model";
import { useModels } from "@/core/models/hooks";
import { useNotification } from "@/core/notification/hooks";
import { useLocalSettings, useThreadSettings } from "@/core/settings";
import type { LocalSettings } from "@/core/settings";
import type { LocalSettingsSetter } from "@/core/settings/store";
import type { AgentThreadState } from "@/core/threads";
import {
  useThreadMetadata,
  useThreadStream,
  useThreadTokenUsage,
} from "@/core/threads/hooks";
import { threadTokenUsageToTokenUsage } from "@/core/threads/token-usage";
import { textOfMessage } from "@/core/threads/utils";
import { env } from "@/env";

import { chatPath, DEFAULT_CHAT_BASE_PATH } from "./chat-path";
import { useSpecificChatMode } from "./use-chat-mode";
import { useHumanInput } from "./use-human-input";
import { useThreadChat } from "./use-thread-chat";

export interface UseChatPageOptions {
  /**
   * Public prefix of the route tree hosting this page — in-app navigation is
   * built as `${basePath}/chats/...`. Defaults to the desktop tree.
   *
   * The hook cannot infer this from the current URL: the post-create navigate
   * uses `history.replaceState`, which issues no request at all, so middleware
   * never sees it and the address bar must be written correctly from here.
   */
  basePath?: string;
  /**
   * Name of the custom agent this page chats with, when the page is an *agent*
   * chat (`…/agents/<name>/chats/<threadId>`) rather than a plain one.
   *
   * The desktop agent page writes `agent_name` by hand in three places; this
   * option is those three places, so the mobile agent page can stay a layout:
   * the stream context, the per-send context, and the clarification-card
   * answers. Omitted (`undefined`) on plain chats — nothing is injected, and
   * every value below is the one the hook produced before this option existed.
   */
  agentName?: string;
}

/**
 * Everything a chat page needs to render the conversation: thread stream state,
 * settings, composer handlers, clarification-card wiring and the derived flags
 * the layout branches on.
 *
 * Both the desktop page (`app/workspace/chats/[thread_id]/page.tsx`) and the
 * mobile chat page call this hook, so behaviour stays in one place and the
 * pages only arrange their own JSX. The redirect to the new-chat route for
 * deleted/inaccessible threads also lives here — it is page-agnostic.
 *
 * `agentName` turns it into the *agent* chat variant (the mobile one; the
 * desktop's `app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx` is
 * the one page that still inlines this behaviour). Passing it is exactly four
 * changes: the thread context carries `agent_name`, every `sendMessage` carries
 * it, clarification answers carry it, and the chat routes move under
 * `/workspace/agents/<name>/`. Not passing it is the plain chat —
 * the object shapes handed downstream are the ones the hook built before the
 * option existed (see `threadContext`, `agentContext` and `handleSubmit`).
 */
export function useChatPage({
  basePath = DEFAULT_CHAT_BASE_PATH,
  agentName,
}: UseChatPageOptions = {}): ChatPageController {
  const router = useRouter();

  /**
   * Public prefix of *this* page's chat routes.
   *
   * An agent chat sits one level deeper than a plain one
   * (`/workspace/agents/<name>/chats/<threadId>`), so the name is folded into
   * the prefix here rather than into `chatPath()`: that helper stays a function
   * of (prefix, threadId), and the two navigations below — `onStart`'s
   * `history.replaceState` and the gone-thread redirect — get the agent
   * address for free. Both are *public* paths, so the middleware lands a phone
   * on the mobile tree without `/m/` ever reaching the address bar (plan
   * §1.2.1). Without `agentName` this is the caller's `basePath`, untouched.
   */
  const chatBasePath = agentName
    ? `${basePath.replace(/\/+$/, "")}/agents/${agentName}`
    : basePath;

  const { threadId, setThreadId, isNewThread, setIsNewThread, isMock } =
    useThreadChat();
  // `isNewThread` tracks whether the backend has the thread yet — gates the
  // SDK's history fetch (see issue #2746).  `isWelcomeMode` is the visual
  // welcome layout (centered input, hero, quick actions); we flip it to false
  // the moment the user submits so the UI animates immediately, even though
  // `isNewThread` stays true until the backend actually creates the thread.
  const [isWelcomeMode, setIsWelcomeMode] = useState(isNewThread);
  const [settings, setSettings] = useThreadSettings(threadId);
  const [localSettings, setLocalSettings] = useLocalSettings();
  const { tokenUsageEnabled } = useModels();
  const threadTokenUsage = useThreadTokenUsage(
    isNewThread || isMock ? undefined : threadId,
    { enabled: tokenUsageEnabled && !isMock },
  );
  const threadMetadata = useThreadMetadata(threadId, {
    enabled: !isNewThread && !isMock,
    isMock,
  });
  const backendTokenUsage = threadTokenUsageToTokenUsage(threadTokenUsage.data);
  const mountedRef = useRef(false);
  useSpecificChatMode();

  useEffect(() => {
    mountedRef.current = true;
  }, []);

  // Keep welcome layout in sync when navigating between threads (sidebar
  // clicks, "new chat" button).  Submitting in /chats/new flips the layout
  // via onSend below — `isNewThread` stays true until onStart, so this effect
  // is harmless during the submit transition.
  useEffect(() => {
    setIsWelcomeMode(isNewThread);
  }, [isNewThread]);

  const { showNotification } = useNotification();

  // The composer's text lives in the prompt-input controller, and the primitive
  // clears it as soon as the promise `handleSubmit` returns resolves. Keeping
  // the controller in a ref lets the error effect below write back to it
  // without re-subscribing on every keystroke (the controller object is rebuilt
  // whenever the input changes).
  const promptTextInput = usePromptInputController().textInput;
  const promptTextInputRef = useRef(promptTextInput);
  promptTextInputRef.current = promptTextInput;

  /**
   * Text of the message the composer last submitted, handed back if its run
   * fails.
   *
   * Why this is needed at all: the prompt primitive treats the submit promise
   * resolving as success and clears the composer, and the LangGraph SDK
   * resolves that promise as soon as the run is *enqueued*
   * (`StreamManager.start` is fire-and-forget — it chains onto an internal
   * queue and returns). A run that fails later therefore cannot reject it, so
   * `prompt-input.tsx`'s own error branch never runs and a failed send is
   * indistinguishable from a successful one at the composer. Keeping the text
   * here is what makes the failure recoverable.
   */
  const submittedTextRef = useRef<string | null>(null);

  /**
   * `agent_name` in the shape the stream wants it, or `undefined` on a plain
   * chat. Memoised because two consumers keep it in dependency arrays
   * (`sendMessage`'s extra context and `useHumanInput`), and a fresh object per
   * render would rebuild their callbacks every render.
   */
  const agentContext = useMemo(
    () => (agentName ? { agent_name: agentName } : undefined),
    [agentName],
  );

  /**
   * Thread context every run is opened with. On an agent chat this is the
   * settings context plus `agent_name` (`useThreadStream` reads it to tag the
   * stream, and it seeds each submit's context). On a plain chat the settings
   * object is passed through **by identity**: it is a dependency of callbacks
   * inside `useThreadStream`, so a fresh object per render would be a
   * behaviour change rather than just an allocation.
   */
  const threadContext = useMemo(
    () =>
      agentContext
        ? { ...settings.context, ...agentContext }
        : settings.context,
    [agentContext, settings.context],
  );

  const {
    thread,
    pendingUsageMessages,
    sendMessage,
    regenerateMessage,
    isUploading,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    displayThreadId: threadId,
    context: threadContext,
    isMock,
    // onSend only animates the UI; do NOT flip `isNewThread` here — the
    // LangGraph SDK eagerly fetches /history the moment it receives a
    // thread id and assumes the thread exists on the backend (issue #2746).
    onSend: () => {
      setIsWelcomeMode(false);
    },
    onStart: (createdThreadId) => {
      // ! Important: Never use next.js router for navigation in this case, otherwise it will cause the thread to re-mount and lose all states. Use native history API instead.
      history.replaceState(null, "", chatPath(chatBasePath, createdThreadId));
      setThreadId(createdThreadId);
      setIsNewThread(false);
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

  const hasThreadMessages = thread.messages.length > 0;

  useEffect(() => {
    if (
      !isNewThread &&
      !isMock &&
      threadMetadata.data === null &&
      !threadMetadata.isLoading &&
      !threadMetadata.isFetching &&
      !isHistoryLoading &&
      !hasMoreHistory &&
      !hasThreadMessages
    ) {
      router.replace(chatPath(chatBasePath));
    }
  }, [
    chatBasePath,
    hasMoreHistory,
    hasThreadMessages,
    isHistoryLoading,
    isMock,
    isNewThread,
    router,
    threadMetadata.data,
    threadMetadata.isFetching,
    threadMetadata.isLoading,
  ]);

  const handleSubmit = useCallback(
    // Always hand the send promise back to the prompt primitive. It treats a
    // non-promise return as synchronous success and clears the composer
    // (`prompt-input.tsx`'s `clearSubmittedState`), so returning `undefined` on
    // the no-attachment path made the two paths disagree — an upload failure
    // kept the message, a plain send did not. Returning it keeps that
    // consistency (and lets the primitive handle a rejection instead of
    // leaving it unhandled).
    //
    // It is not sufficient on its own: see `submittedTextRef` — the SDK
    // resolves this promise before the run even starts, so the failure path
    // additionally has to hand the text back.
    (message: PromptInputMessage) => {
      submittedTextRef.current = message.text.trim() ? message.text : null;
      // An agent chat carries `agent_name` on the send as well: the stream
      // context above covers the subscription, this covers the run's own
      // payload. A plain chat calls `sendMessage` with the two arguments it
      // always did — not with a trailing `undefined`.
      return agentContext
        ? sendMessage(threadId, message, agentContext)
        : sendMessage(threadId, message);
    },
    [agentContext, sendMessage, threadId],
  );

  // A failed run cannot reject the submit promise (see `submittedTextRef`), so
  // the composer is told about the failure here instead: the run's error state
  // is the one place the send pipeline reports it. The text is put back only
  // when the composer is still empty — never over something the user has typed
  // since.
  const threadError = thread.error;
  useEffect(() => {
    if (!threadError) {
      return;
    }
    const text = submittedTextRef.current;
    submittedTextRef.current = null;
    if (!text || !mountedRef.current) {
      return;
    }
    const input = promptTextInputRef.current;
    if (input.value.trim()) {
      return;
    }
    input.setInput(text);
  }, [threadError]);

  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);
  const handleRegenerate = useCallback(
    (messageId: string, supersededMessageIds: string[]) =>
      regenerateMessage(threadId, messageId, supersededMessageIds),
    [regenerateMessage, threadId],
  );

  const tokenUsageInlineMode: TokenUsageInlineMode = tokenUsageEnabled
    ? localSettings.tokenUsage.inlineMode
    : "off";
  const hasTodos = (thread.values.todos?.length ?? 0) > 0;

  // Strict mode: while a clarification card is open the composer is disabled
  // and regenerate is blocked. See useHumanInput.
  const { hasOpenHumanInputCard, handleSubmitHumanInput } = useHumanInput({
    threadId,
    sendMessage,
    messages: thread.messages,
    extraContext: agentContext,
    enabled: !isMock && env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true",
  });

  const isDemoMode = env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true";

  return {
    threadId,
    isMock,
    isNewThread,
    isWelcomeMode,
    thread,
    messages: thread.messages,
    title: thread.values.title,
    isBusy: thread.isLoading,
    pendingUsageMessages,
    backendTokenUsage,
    tokenUsageEnabled,
    tokenUsageInlineMode,
    settings,
    setSettings,
    localSettings,
    setLocalSettings,
    isUploading,
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
    isInputDisabled:
      isMock || isDemoMode || isUploading || hasOpenHumanInputCard,
    canRegenerate:
      !isNewThread &&
      !isMock &&
      !isDemoMode &&
      !isUploading &&
      !thread.isLoading &&
      !hasOpenHumanInputCard,
  };
}

export type ChatPageController = {
  /** Thread currently displayed; a fresh uuid before the backend creates it. */
  threadId: string;
  /** `?mock=true` — all fetches are stubbed. */
  isMock: boolean;
  /** The backend does not have this thread yet (URL is `.../new`). */
  isNewThread: boolean;
  /** Centered welcome layout, flipped off as soon as the user submits. */
  isWelcomeMode: boolean;
  /** Live LangGraph stream: `messages`, `values`, `isLoading`, `error`, `stop()`. */
  thread: BaseStream<AgentThreadState>;
  /** Convenience alias of `thread.messages`. */
  messages: Message[];
  /** Thread title as reported by the stream. */
  title: string;
  /** A run is streaming. */
  isBusy: boolean;
  /** Messages sent this turn, used by the header token-usage indicator. */
  pendingUsageMessages: Message[];
  /** Server-reported token usage mapped to the display shape. */
  backendTokenUsage: ReturnType<typeof threadTokenUsageToTokenUsage>;
  /** Whether the backend reports token usage at all. */
  tokenUsageEnabled: boolean;
  /** Resolved inline token-usage rendering mode (`off` when disabled). */
  tokenUsageInlineMode: TokenUsageInlineMode;
  /** Per-thread settings (model, mode, generation models) + setter. */
  settings: LocalSettings;
  setSettings: LocalSettingsSetter;
  /** Locally persisted settings shared by all threads + setter. */
  localSettings: LocalSettings;
  setLocalSettings: LocalSettingsSetter;
  /** Files are being uploaded before submit. */
  isUploading: boolean;
  /** Older history is still loading. */
  isHistoryLoading: boolean;
  /** Older history exists. */
  hasMoreHistory: boolean;
  /** Load the next page of history. */
  loadMoreHistory: () => void | Promise<void>;
  /** A clarification card awaits an answer (composer disabled, regenerate blocked). */
  hasOpenHumanInputCard: boolean;
  /** Answer the open clarification card; resolves to `true` once sent. */
  handleSubmitHumanInput: (
    request: HumanInputRequest,
    response: HumanInputResponse,
  ) => Promise<boolean>;
  /** Send a composer message; resolves once the upload round-trip finishes. */
  handleSubmit: (message: PromptInputMessage) => void | Promise<void>;
  /** Stop the current run. */
  handleStop: () => Promise<void>;
  /**
   * Regenerate an assistant message and supersede the listed siblings. Returns
   * the in-flight promise so the caller can await it (MessageList keeps its
   * per-row spinner up until it settles).
   */
  handleRegenerate: (
    messageId: string,
    supersededMessageIds: string[],
  ) => void | Promise<void>;
  /** The thread publishes todos, so the todo panel renders above the composer. */
  hasTodos: boolean;
  /** Flips true after mount — gates the composer to avoid a hydration flash. */
  mountedRef: RefObject<boolean>;
  /** `NEXT_PUBLIC_STATIC_WEBSITE_ONLY` build (demo site). */
  isDemoMode: boolean;
  /** Composer must be disabled (mock / demo / uploading / clarification open). */
  isInputDisabled: boolean;
  /** Message-list regenerate action is allowed. */
  canRegenerate: boolean;
};
