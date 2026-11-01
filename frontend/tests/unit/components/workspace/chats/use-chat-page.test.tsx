import {
  afterEach,
  beforeEach,
  describe,
  expect,
  rs,
  test,
} from "@rstest/core";
import { cleanup, renderHook, waitFor } from "@testing-library/react";

import { useChatPage } from "@/components/workspace/chats/use-chat-page";

/**
 * The one contract an agent chat adds to `useChatPage()` — and the one it must
 * not change for plain chats.
 *
 * Two properties are under test:
 *
 *   1. **`agentName` is threaded into exactly four places**: the stream
 *      context, `sendMessage`'s third argument, the clarification-card answers
 *      (`extraContext`), and the chat routes (`/workspace/agents/<name>/…`).
 *      The desktop agent page writes the first three by hand; this hook is now
 *      the second implementation of that, so each of the four is asserted
 *      against the arguments the hook actually hands downstream rather than
 *      against a rendered DOM.
 *   2. **Without `agentName` nothing moved.** The plain chat page is the
 *      regression surface of a shared hook, so the assertions here are about
 *      *identity and arity*, not equivalence: the context object is the very
 *      object `useThreadSettings` returned (not a copy with the same keys —
 *      `useThreadStream` memoises on that identity), the send call has the two
 *      arguments it always had (no trailing `undefined`), and the stream
 *      options carry the same key set as before.
 *
 * Everything the hook touches is mocked, including the two hooks that live
 * beside it: `useThreadChat` (route state) and `useHumanInput` (whose own
 * behaviour — including that its `extraContext` reaches `sendMessage` — is
 * covered by `use-human-input.test.ts`).
 */

const mockThreadId = "11111111-1111-1111-1111-111111111111";

const mocks = rs.hoisted(() => {
  /** `useThreadSettings().context` — the object the stream is opened with. */
  const settingsContext = {
    model_name: undefined,
    mode: "flash" as const,
  };

  return {
    settingsContext,
    /** What `useThreadSettings` returns: settings *containing* that context. */
    settings: { context: settingsContext },
    streamOptions: [] as Record<string, unknown>[],
    humanInputOptions: [] as Record<string, unknown>[],
    // Typed so the recorded call tuples are readable (`mock.calls[0]![2]`).
    sendMessage: rs.fn(
      async (
        _threadId: string,
        _message: { text: string; files: unknown[] },
        _extraContext?: Record<string, unknown>,
      ) => undefined,
    ),
    routerReplace: rs.fn(),
    setSettings: rs.fn(),
    isNewThread: false,
    isMock: false,
    /** `null` makes the "thread is gone" branch of the hook fire. */
    metadata: { data: {} as unknown },
  };
});

rs.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.routerReplace, push: rs.fn() }),
}));

rs.mock("@/env", () => ({
  env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: undefined },
}));

rs.mock("@/components/ai-elements/prompt-input", () => ({
  usePromptInputController: () => ({
    textInput: { value: "", setInput: rs.fn() },
  }),
}));

rs.mock("@/core/models/hooks", () => ({
  useModels: () => ({ tokenUsageEnabled: false }),
}));

rs.mock("@/core/notification/hooks", () => ({
  useNotification: () => ({ showNotification: rs.fn() }),
}));

rs.mock("@/core/settings", () => ({
  useThreadSettings: () => [mocks.settings, mocks.setSettings],
  useLocalSettings: () => [
    { tokenUsage: { headerTotal: true, inlineMode: "per_turn" } },
    rs.fn(),
  ],
}));

rs.mock("@/core/threads/hooks", () => ({
  useThreadStream: (options: Record<string, unknown>) => {
    mocks.streamOptions.push(options);
    return {
      thread: {
        messages: [],
        values: {},
        isLoading: false,
        error: undefined,
        stop: rs.fn(),
      },
      pendingUsageMessages: [],
      sendMessage: mocks.sendMessage,
      regenerateMessage: rs.fn(),
      isUploading: false,
      isHistoryLoading: false,
      hasMoreHistory: false,
      loadMoreHistory: rs.fn(),
    };
  },
  useThreadMetadata: () => ({
    data: mocks.metadata.data,
    isLoading: false,
    isFetching: false,
  }),
  useThreadTokenUsage: () => ({ data: undefined }),
}));

rs.mock("@/components/workspace/chats/use-chat-mode", () => ({
  useSpecificChatMode: () => undefined,
}));

rs.mock("@/components/workspace/chats/use-thread-chat", () => ({
  useThreadChat: () => ({
    threadId: mockThreadId,
    setThreadId: rs.fn(),
    isNewThread: mocks.isNewThread,
    setIsNewThread: rs.fn(),
    isMock: mocks.isMock,
  }),
}));

rs.mock("@/components/workspace/chats/use-human-input", () => ({
  useHumanInput: (options: Record<string, unknown>) => {
    mocks.humanInputOptions.push(options);
    return {
      hasOpenHumanInputCard: false,
      handleSubmitHumanInput: rs.fn(async () => true),
    };
  },
}));

beforeEach(() => {
  mocks.streamOptions = [];
  mocks.humanInputOptions = [];
  mocks.isNewThread = false;
  mocks.isMock = false;
  mocks.metadata = { data: {} };
});

afterEach(() => {
  cleanup();
  rs.clearAllMocks();
});

function renderChatPage(options?: { agentName?: string }) {
  return renderHook(() => useChatPage(options));
}

/** Last options object handed to `useThreadStream`. */
function streamOptions() {
  return mocks.streamOptions.at(-1)!;
}

describe("useChatPage without agentName (the plain chat page)", () => {
  test("the stream context is the settings object itself, not a copy", () => {
    renderChatPage();

    // Identity, not equality: `useThreadStream` keeps `context` in dependency
    // arrays, so a spread with the same keys would still be a change.
    expect(streamOptions().context).toBe(mocks.settingsContext);
  });

  test("handleSubmit keeps the two-argument send call", () => {
    const { result } = renderChatPage();
    const message = { text: "hello", files: [] };

    void result.current.handleSubmit(message);

    expect(mocks.sendMessage).toHaveBeenCalledTimes(1);
    // Arity is the assertion: `sendMessage(id, message, undefined)` would be a
    // third argument the SDK path never used to receive.
    expect(mocks.sendMessage.mock.calls[0]).toHaveLength(2);
    expect(mocks.sendMessage.mock.calls[0]![0]).toBe(mockThreadId);
    expect(mocks.sendMessage.mock.calls[0]![1]).toEqual(message);
  });

  test("clarification answers carry no agent context", () => {
    renderChatPage();

    expect(mocks.humanInputOptions.at(-1)!.extraContext).toBeUndefined();
  });

  test("the stream options carry the same keys as before the option existed", () => {
    renderChatPage();

    expect(Object.keys(streamOptions()).sort()).toEqual([
      "context",
      "displayThreadId",
      "isMock",
      "onFinish",
      "onSend",
      "onStart",
      "threadId",
    ]);
  });

  test("a created thread is written to the plain chat address", () => {
    const replaceState = rs.spyOn(window.history, "replaceState");
    renderChatPage();

    (streamOptions().onStart as (id: string) => void)("new-thread-id");

    expect(replaceState).toHaveBeenCalledWith(
      null,
      "",
      "/workspace/chats/new-thread-id",
    );
    replaceState.mockRestore();
  });

  test("a thread the backend does not know redirects to the plain new-chat route", async () => {
    mocks.metadata = { data: null };
    renderChatPage();

    await waitFor(() => {
      expect(mocks.routerReplace).toHaveBeenCalledWith("/workspace/chats/new");
    });
  });
});

describe("useChatPage with agentName (the agent chat page)", () => {
  test("the thread context carries agent_name, on top of the settings context", () => {
    renderChatPage({ agentName: "researcher" });

    const context = streamOptions().context as Record<string, unknown>;
    expect(context.agent_name).toBe("researcher");
    // Everything the settings context had is still there (the spread adds,
    // it does not replace).
    expect(context.mode).toBe("flash");
    expect(context).not.toBe(mocks.settingsContext);
  });

  test("handleSubmit hands agent_name to sendMessage as the third argument", () => {
    const { result } = renderChatPage({ agentName: "researcher" });
    const message = { text: "hello", files: [] };

    void result.current.handleSubmit(message);

    expect(mocks.sendMessage.mock.calls[0]).toHaveLength(3);
    expect(mocks.sendMessage.mock.calls[0]![0]).toBe(mockThreadId);
    expect(mocks.sendMessage.mock.calls[0]![1]).toEqual(message);
    expect(mocks.sendMessage.mock.calls[0]![2]).toEqual({
      agent_name: "researcher",
    });
  });

  test("clarification answers carry agent_name", () => {
    renderChatPage({ agentName: "researcher" });

    expect(mocks.humanInputOptions.at(-1)!.extraContext).toEqual({
      agent_name: "researcher",
    });
  });

  test("the stream options key set is identical to the plain chat's", () => {
    renderChatPage({ agentName: "researcher" });

    expect(Object.keys(streamOptions()).sort()).toEqual([
      "context",
      "displayThreadId",
      "isMock",
      "onFinish",
      "onSend",
      "onStart",
      "threadId",
    ]);
  });

  test("a created thread is written to the agent's own chat address", () => {
    const replaceState = rs.spyOn(window.history, "replaceState");
    renderChatPage({ agentName: "researcher" });

    (streamOptions().onStart as (id: string) => void)("new-thread-id");

    // Public path, one level deeper than a plain chat — and never `/m/…`.
    expect(replaceState).toHaveBeenCalledWith(
      null,
      "",
      "/workspace/agents/researcher/chats/new-thread-id",
    );
    replaceState.mockRestore();
  });

  test("a gone thread redirects under the agent, not to the plain new chat", async () => {
    mocks.metadata = { data: null };
    renderChatPage({ agentName: "researcher" });

    await waitFor(() => {
      expect(mocks.routerReplace).toHaveBeenCalledWith(
        "/workspace/agents/researcher/chats/new",
      );
    });
  });

  test("a basePath override still prefixes the agent route", () => {
    const replaceState = rs.spyOn(window.history, "replaceState");
    renderChatPage({ agentName: "researcher" });
    cleanup();

    renderHook(() =>
      useChatPage({ agentName: "researcher", basePath: "/m/workspace/" }),
    );
    (streamOptions().onStart as (id: string) => void)("new-thread-id");

    expect(replaceState).toHaveBeenCalledWith(
      null,
      "",
      "/m/workspace/agents/researcher/chats/new-thread-id",
    );
    replaceState.mockRestore();
  });
});
