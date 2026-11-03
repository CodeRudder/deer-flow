import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { afterEach, beforeAll, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { MessageList } from "@/components/workspace/messages/message-list";
import type { AgentThreadState } from "@/core/threads";
import type { PendingInterjection } from "@/core/threads/interjection";

rs.mock("@/env", () => ({
  env: {
    NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false",
    NEXT_PUBLIC_PROCESS_TIMELINE: "true",
  },
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: {
        thinking: "thinking",
        loadMore: "more",
        loading: "loading",
        regenerate: "regenerate",
      },
      interjection: { queuedHint: "queued", undoTooltip: "undo" },
      humanInput: {},
      pages: { newChat: "New Chat" },
      subtasks: { failed: "failed", executing: (n: number) => `${n}` },
      toolCalls: {
        searchForRelatedInfo: "search for related info",
        searchFor: (q: string) => `search for ${q}`,
        searchOnWebFor: (q: string) => `search the web for ${q}`,
        searchForRelatedImages: "search images",
        searchForRelatedImagesFor: (q: string) => `image search ${q}`,
        viewWebPage: "view page",
        listFolder: "list folder",
        readFile: "read file",
        writeFile: "write file",
        executeCommand: "execute command",
        writeTodos: "write todos",
        durationMinutes: () => "0m",
        durationSeconds: () => "0s",
        useTool: (name: string) => `use ${name}`,
        lessSteps: "less",
        moreSteps: (n: number) => `${n} more`,
        executedSteps: (n: number) => `ran ${n} steps`,
        toolsUsed: (n: number) => `${n} tools`,
      },
      tokenUsage: { label: "tokens", unavailableShort: "n/a" },
      clipboard: {
        copyToClipboard: "copy",
        failedToCopyToClipboard: "copy failed",
        copied: "copied",
      },
      processTimeline: {
        narrationLabel: "Note",
        viewMore: (count: number) => `View more (${count})`,
        viewLess: "Collapse",
        openArtifact: "Open artifact",
      },

      thinking: {},
      messages: {},
    },
  }),
}));

rs.mock("@/core/subagents/hooks", () => ({
  useSubtaskStatuses: () => ({ data: null }),
}));

rs.mock("@/core/tasks/context", () => ({
  useUpdateSubtask: () => () => undefined,
}));

rs.mock("@/components/workspace/subtask-detail-sheet", () => ({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  SubtaskDetailSheet: () => null as any,
}));

rs.mock("@/components/workspace/artifacts", () => ({
  useArtifacts: () => ({
    setOpen: () => undefined,
    select: () => undefined,
    autoOpen: false,
    autoSelect: false,
    selectedArtifact: null,
  }),
}));

function fakeThread(messages: Message[]): BaseStream<AgentThreadState> {
  return {
    messages,
    values: { messages },
    isLoading: false,
    isThreadLoading: false,
    error: undefined,
    getMessagesMetadata: () => undefined,
    stop: () => undefined,
    submit: () => undefined,
  } as unknown as BaseStream<AgentThreadState>;
}

const MULTI_STEP: Message[] = [
  { id: "h1", type: "human", content: "research and draft" } as Message,
  {
    id: "a1",
    type: "ai",
    content: "",
    additional_kwargs: { reasoning_content: "searching first" },
    tool_calls: [
      { id: "tc1", name: "web_search", args: { query: "deer flow" } },
    ],
  } as unknown as Message,
  {
    id: "t1",
    type: "tool",
    name: "web_search",
    tool_call_id: "tc1",
    content: JSON.stringify([{ url: "https://example.com", title: "Example" }]),
  } as Message,
  { id: "a2", type: "ai", content: "final answer text" } as Message,
];

// jsdom lacks ResizeObserver (used by the conversation/tooltip primitives).
beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    /* eslint-disable @typescript-eslint/no-empty-function */
    observe() {}
    unobserve() {}
    disconnect() {}
    /* eslint-enable @typescript-eslint/no-empty-function */
  } as unknown as typeof ResizeObserver;
});

afterEach(() => cleanup());

describe("MessageList — process timeline flag on", () => {
  it("renders single-line step rows instead of the processing panel", () => {
    const { queryByTestId, getByText } = render(
      <MessageList
        threadId="t"
        thread={fakeThread(MULTI_STEP)}
        processTimeline={true}
      />,
    );

    // reasoning + toolCall steps; the settled content-only message is an answer
    expect(
      document.querySelectorAll('[data-testid="process-step-row"]'),
    ).toHaveLength(2);
    expect(getByText("search the web for deer flow")).toBeTruthy();
    expect(getByText("final answer text")).toBeTruthy();
    // answer reasoning renders once — inside the bubble only (#3868)
    expect(queryByTestId("process-step-detail")).toBeNull();
    // no legacy collapsed-summary card either
    expect(queryByTestId("mobile-collapsed-steps")).toBeNull();
  });

  it("expands a step on click showing the left-ruled detail", () => {
    render(
      <MessageList
        threadId="t"
        thread={fakeThread(MULTI_STEP)}
        processTimeline={true}
      />,
    );
    expect(
      document.querySelectorAll('[data-testid="process-step-detail"]'),
    ).toHaveLength(0);
    const toggles = document.querySelectorAll(
      '[data-testid="process-step-toggle"]',
    );
    fireEvent.click(toggles[1]!);
    expect(
      document.querySelectorAll('[data-testid="process-step-detail"]'),
    ).toHaveLength(1);
  });

  it("req 4 — only the last in-progress row's collapsed preview scrolls with the stream (tail lines)", () => {
    const reasoning = Array.from(
      { length: 8 },
      (_, i) => `thought-${i + 1}`,
    ).join("\n");
    const streamingMessages: Message[] = [
      { id: "h1", type: "human", content: "go" } as Message,
      {
        id: "a1",
        type: "ai",
        content: reasoning,
        tool_calls: [{ id: "tc1", name: "ls", args: {} }],
      } as unknown as Message,
    ];
    const thread = {
      ...fakeThread(streamingMessages),
      isLoading: true,
      isThreadLoading: false,
    };
    render(<MessageList threadId="t" thread={thread} processTimeline={true} />);
    const preview = document.querySelector(
      '[data-testid="process-step-preview"]',
    );
    // narration preview = last 2 lines while streaming (req 4)
    expect(preview?.textContent).toContain("thought-8");
    expect(preview?.textContent).toContain("thought-7");
    // tail, not head
    expect(preview?.textContent).not.toContain("thought-1");
  });

  it("renders queued interjections after the answer entries", () => {
    const item: PendingInterjection = {
      id: "i1",
      thread_id: "t",
      run_id: "r",
      text: "queued addition",
      source: "web",
      seq: 1,
      created_at: "2026-11-03T00:00:00Z",
      applied: false,
    };
    const { getByText } = render(
      <MessageList
        threadId="t"
        thread={fakeThread(MULTI_STEP)}
        processTimeline={true}
        pendingInterjections={[item]}
      />,
    );
    const bodyText = document.body.textContent;
    expect(
      bodyText.includes("queued addition"),
      `BODY: ${bodyText.slice(0, 600)}`,
    ).toBe(true);
    expect(getByText("queued")).toBeTruthy();
  });
});
