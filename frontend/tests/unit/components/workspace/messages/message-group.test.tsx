import type { Message } from "@langchain/langgraph-sdk";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";

import { MessageGroup } from "@/components/workspace/messages/message-group";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { thinking: "thinking" },
      toolCalls: {
        lessSteps: "less steps",
        moreSteps: (count: number) => `${count} more steps`,
        readFile: "read file",
        useTool: (name: string) => `use ${name}`,
        searchForRelatedInfo: "search for related info",
        searchOnWebFor: (query: string) => `search the web for ${query}`,
      },
      tokenUsage: {
        label: "tokens",
        unavailableShort: "n/a",
      },
    },
  }),
}));

rs.mock("@/env", () => ({
  env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" },
}));

rs.mock("@/components/workspace/artifacts", () => ({
  useArtifacts: () => ({
    setOpen: () => undefined,
    autoOpen: false,
    autoSelect: false,
    selectedArtifact: null,
    select: () => undefined,
  }),
}));

afterEach(cleanup);

function renderGroup(messages: Message[], isLoading = false) {
  // Streaming rehype splits words into spans; assert on textContent.
  return render(<MessageGroup messages={messages} isLoading={isLoading} />)
    .container.textContent;
}

describe("MessageGroup", () => {
  it("renders unresolved streaming assistant text before a tool call arrives", () => {
    const text = renderGroup(
      [
        {
          id: "ai-1",
          type: "ai",
          content: "I will inspect the source material first.",
        } as Message,
      ],
      true,
    );

    expect(text).toContain("I will inspect the source material first.");
  });

  it("renders assistant text attached to a tool-calling processing message", () => {
    const text = renderGroup([
      {
        id: "ai-1",
        type: "ai",
        content: "I will inspect the current implementation.",
        tool_calls: [
          {
            id: "call-1",
            name: "read_file",
            args: { path: "message-group.tsx" },
          },
        ],
      } as Message,
      {
        id: "tool-1",
        type: "tool",
        name: "read_file",
        tool_call_id: "call-1",
        content: "file contents",
      } as Message,
    ]);

    expect(text).toContain("I will inspect the current implementation.");
    expect(text).toContain("message-group.tsx");
  });

  it("keeps content-only assistant text visible after a tool call while streaming", () => {
    const text = renderGroup(
      [
        {
          id: "ai-1",
          type: "ai",
          content: "I will inspect the current implementation.",
          tool_calls: [
            {
              id: "call-1",
              name: "read_file",
              args: { path: "source.ts" },
            },
          ],
        } as Message,
        {
          id: "tool-1",
          type: "tool",
          name: "read_file",
          tool_call_id: "call-1",
          content: "file contents",
        } as Message,
        {
          id: "ai-2",
          type: "ai",
          content: "Here is the final streamed answer.",
        } as Message,
      ],
      true,
    );

    expect(text).toContain("Here is the final streamed answer.");
  });

  it("keeps reasoning-only processing groups unchanged", () => {
    const text = renderGroup([
      {
        id: "ai-1",
        type: "ai",
        content: "",
        additional_kwargs: { reasoning_content: "I should search first." },
        tool_calls: [
          { id: "call-1", name: "web_search", args: { query: "x" } },
        ],
      } as Message,
      {
        id: "tool-1",
        type: "tool",
        name: "web_search",
        tool_call_id: "call-1",
        content: "[]",
      } as Message,
    ]);

    // Reasoning above the last tool call stays collapsed behind "more steps".
    expect(text).toContain("1 more steps");
    expect(text).toContain("search the web for x");
    expect(text).not.toContain("I should search first.");
  });

  it("keeps clarification text and the question row out of the step panel", () => {
    const text = renderGroup([
      {
        id: "ai-1",
        type: "ai",
        content: "Here is the updated table before asking.",
        additional_kwargs: { reasoning_content: "I need user confirmation." },
        tool_calls: [
          {
            id: "call-1",
            name: "ask_clarification",
            args: { question: "Shall I continue?" },
          },
        ],
      } as Message,
      {
        id: "tool-1",
        type: "tool",
        name: "ask_clarification",
        tool_call_id: "call-1",
        content: "Shall I continue?",
      } as Message,
    ]);

    // Panel keeps only the reasoning trace; text/question live outside.
    expect(text).not.toContain("Here is the updated table before asking.");
    expect(text).not.toContain("Shall I continue?");
    expect(text).toContain("thinking");
    expect(text).not.toContain("I need user confirmation.");
  });

  it("renders nothing for a clarification message without reasoning", () => {
    const { container } = render(
      <MessageGroup
        messages={[
          {
            id: "ai-1",
            type: "ai",
            content: "Question preamble text.",
            tool_calls: [
              {
                id: "call-1",
                name: "ask_clarification",
                args: { question: "Shall I continue?" },
              },
            ],
          } as Message,
        ]}
      />,
    );

    expect(container.innerHTML).toBe("");
  });
});
