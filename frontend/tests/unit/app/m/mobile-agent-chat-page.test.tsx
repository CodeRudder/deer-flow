import {
  afterEach,
  beforeEach,
  describe,
  expect,
  rs,
  test,
} from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import MobileAgentChatPage from "@/app/m/(app)/(fullbleed)/workspace/agents/[agent_name]/chats/[thread_id]/page";

/**
 * The mobile agent chat screen is a *layout*: everything behavioural is
 * `useChatPage({ agentName })`, and the rest is the mobile shell. This file
 * asserts the three things the page itself decides — and nothing about the
 * shared components it arranges, which have their own suites:
 *
 *   1. it passes the route's `agent_name` to the hook (that single line is what
 *      turns the three `agent_name` injections and the `/workspace/agents/…`
 *      routes on — see `use-chat-page.test.tsx` for what the hook does with it);
 *   2. the header is handed the **agent's** name, not the thread title, and its
 *      back button points at `/agents` (the mobile gallery), not `/workspace`;
 *   3. the screen really is a chat: the versioned composer is mounted (the
 *      screen it replaces was a 404, so "it renders at all and can send" is the
 *      regression this guards).
 *
 * The composer and the header are stubs here on purpose — the real ones are
 * exercised in `mobile-chat-*.spec.ts` E2E, where a 390px viewport exists.
 */

const mocks = rs.hoisted(() => ({
  chatPageOptions: [] as Record<string, unknown>[],
  headerProps: [] as Record<string, unknown>[],
  /** `null` = the detail request has not landed (or failed) yet. */
  agent: null as { name: string } | null,
  routerPush: rs.fn(),
}));

rs.mock("next/navigation", () => ({
  useParams: () => ({
    agent_name: "researcher",
    thread_id: "11111111-1111-1111-1111-111111111111",
  }),
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: mocks.routerPush, replace: rs.fn() }),
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    locale: "en-US",
    t: {
      pages: { appName: "DeerFlow", chats: "Chats" },
      agents: { title: "Agents" },
      chats: { scrollToBottom: "Scroll to bottom" },
      humanInput: { inputDisabledHint: "Answer the question first" },
    },
  }),
}));

rs.mock("@/core/agents", () => ({
  useAgent: () => ({ agent: mocks.agent, isLoading: false, error: null }),
}));

rs.mock("@/components/workspace/chats", () => ({
  useChatPage: (options: Record<string, unknown>) => {
    mocks.chatPageOptions.push(options);
    return {
      threadId: "11111111-1111-1111-1111-111111111111",
      isMock: false,
      isNewThread: false,
      isWelcomeMode: false,
      thread: {
        messages: [],
        values: {},
        isLoading: false,
        error: undefined,
        stop: rs.fn(),
      },
      title: "A thread title that must not be the header",
      pendingUsageMessages: [],
      backendTokenUsage: null,
      tokenUsageEnabled: false,
      tokenUsageInlineMode: "off" as const,
      settings: { context: {} },
      setSettings: rs.fn(),
      localSettings: { tokenUsage: {} },
      setLocalSettings: rs.fn(),
      isUploading: false,
      isHistoryLoading: false,
      hasMoreHistory: false,
      loadMoreHistory: rs.fn(),
      hasOpenHumanInputCard: false,
      handleSubmitHumanInput: rs.fn(),
      handleSubmit: rs.fn(),
      handleStop: rs.fn(),
      handleRegenerate: rs.fn(),
      hasTodos: false,
      mountedRef: { current: true },
      isDemoMode: false,
      isInputDisabled: false,
      canRegenerate: false,
    };
  },
}));

rs.mock("@/components/workspace/messages", () => ({
  MessageList: () => null,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM: 0,
}));

rs.mock("@/components/workspace/mobile/chat-header", () => ({
  MobileChatHeader: (props: Record<string, unknown>) => {
    mocks.headerProps.push(props);
    return (
      <div
        data-testid="mobile-chat-header"
        data-title={String(props.title)}
        data-back-href={String(props.backHref)}
        data-back-label={String(props.backLabel)}
      />
    );
  },
}));

rs.mock("@/components/workspace/mobile/composer", () => ({
  MobileComposer: () => <div data-testid="mobile-composer" />,
}));

rs.mock("@/components/workspace/mobile/use-scroll-to-bottom", () => ({
  useScrollToBottom: () => ({ isAtBottom: true, scrollToBottom: rs.fn() }),
}));

// The shell's providers: passthroughs, so the assertions stay on the page.
rs.mock("@/components/ai-elements/prompt-input", () => ({
  PromptInputProvider: ({ children }: { children: React.ReactNode }) =>
    children,
}));

rs.mock("@/components/ui/sidebar", () => ({
  SidebarProvider: ({ children }: { children: React.ReactNode }) => children,
}));

rs.mock("@/core/tasks/context", () => ({
  SubtasksProvider: ({ children }: { children: React.ReactNode }) => children,
}));

rs.mock("@/components/workspace/artifacts", () => ({
  ArtifactsProvider: ({ children }: { children: React.ReactNode }) => children,
  useArtifacts: () => ({ setArtifacts: rs.fn(), artifacts: [] }),
}));

beforeEach(() => {
  mocks.chatPageOptions = [];
  mocks.headerProps = [];
  mocks.agent = null;
});

afterEach(() => {
  cleanup();
  rs.clearAllMocks();
});

describe("mobile agent chat page", () => {
  test("hands the route's agent_name to useChatPage", () => {
    render(<MobileAgentChatPage />);

    expect(mocks.chatPageOptions).toHaveLength(1);
    expect(mocks.chatPageOptions[0]).toEqual({ agentName: "researcher" });
  });

  test("titles the screen with the agent and goes back to /agents", () => {
    render(<MobileAgentChatPage />);

    const header = mocks.headerProps.at(-1)!;
    // The route param stands in until the agent detail lands…
    expect(header.title).toBe("researcher");
    expect(header.backHref).toBe("/agents");
    expect(header.backLabel).toBe("Agents");
    // …and the thread title the hook also returns is deliberately unused.
    expect(header.title).not.toBe("A thread title that must not be the header");
  });

  test("prefers the fetched agent name when it has landed", () => {
    mocks.agent = { name: "Researcher Pro" };
    render(<MobileAgentChatPage />);

    expect(mocks.headerProps.at(-1)!.title).toBe("Researcher Pro");
  });

  test("mounts the mobile composer, so the screen can actually send", () => {
    render(<MobileAgentChatPage />);

    expect(screen.getByTestId("mobile-composer")).toBeTruthy();
    expect(screen.getByTestId("mobile-chat-header")).toBeTruthy();
  });
});
