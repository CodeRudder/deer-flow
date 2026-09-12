import type { Message } from "@langchain/langgraph-sdk";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { MessageGroup } from "@/components/workspace/messages/message-group";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { thinking: "thinking" },
      toolCalls: {
        lessSteps: "less steps",
        moreSteps: (count: number) => `${count} more steps`,
        executedSteps: (count: number) => `ran ${count} steps`,
        toolsUsed: (count: number) => `${count} tools`,
        readFile: "read file",
        writeFile: "write file",
        listFolder: "list folder",
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

/** The preamble a model writes before it calls a tool. */
const PREAMBLE = "I will inspect the current implementation.";

/**
 * One message, content *and* tool calls: the shape the mobile transcript gets
 * when the model says something and then reaches for a tool. Two tool calls
 * plus one assistant text is the smallest group where "count what is folded"
 * and "count every step" disagree.
 */
const TWO_READS_AND_TEXT = [
  {
    id: "ai-1",
    type: "ai",
    content: PREAMBLE,
    tool_calls: [
      { id: "call-1", name: "read_file", args: { path: "a.ts" } },
      { id: "call-2", name: "read_file", args: { path: "b.ts" } },
    ],
  } as Message,
];

function collapsedSummary(container: HTMLElement) {
  return container.querySelector<HTMLButtonElement>(
    "[data-testid='mobile-collapsed-steps']",
  );
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

  it("counts only the folded rows and keeps the answer out of the summary", () => {
    const { container } = render(
      <MessageGroup collapsedSteps messages={TWO_READS_AND_TEXT} />,
    );
    const text = container.textContent ?? "";

    // Two rows fold — the two tool calls. The assistant text is not one of
    // them, so the summary must not claim three (`AGENTS.md`: assistant text
    // renders in collapsed state, and the summary counts the rows behind it).
    expect(text).toContain("ran 2 steps");
    expect(text).toContain("2 tools");
    expect(text).not.toContain("ran 3 steps");

    // The answer stays readable while the rows are folded …
    expect(text).toContain(PREAMBLE);
    // … and paints after the summary row (prototype ②: the `.disc` first, the
    // `.bubble` below it).
    expect(text.indexOf("ran 2 steps")).toBeLessThan(text.indexOf(PREAMBLE));
    // The folded rows themselves are not painted before the tap.
    expect(collapsedSummary(container)?.getAttribute("aria-expanded")).toBe(
      "false",
    );
    expect(text).not.toContain("a.ts");
    expect(text).not.toContain("b.ts");
  });

  it("expands into exactly the rows the summary counted, answer still outside", () => {
    const { container } = render(
      <MessageGroup collapsedSteps messages={TWO_READS_AND_TEXT} />,
    );
    const summary = collapsedSummary(container)!;
    const panel = summary.parentElement!;

    fireEvent.click(summary);

    expect(summary.getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("a.ts");
    expect(container.textContent).toContain("b.ts");
    // The expansion holds one row per counted step — a badge per `read_file` —
    // and not the answer: the panel's text is the summary plus those rows.
    expect(panel.querySelectorAll("[data-slot='badge']")).toHaveLength(2);
    expect(panel.textContent).not.toContain(PREAMBLE);
    expect(summary.textContent).not.toContain(PREAMBLE);
    expect(container.textContent).toContain(PREAMBLE);

    fireEvent.click(summary);

    expect(summary.getAttribute("aria-expanded")).toBe("false");
    expect(container.textContent).not.toContain("a.ts");
    // Folding the rows back must not take the answer with them, or a streaming
    // turn that already called a tool would go blank.
    expect(container.textContent).toContain(PREAMBLE);
  });

  it("keeps every streaming text visible, in order, while collapsed", () => {
    // The turn is not over: the last AI message is content-only and still
    // streaming into the same processing group, so the group holds two
    // `assistantText` steps around one tool call. Both have to paint, in the
    // order they arrived — this is the window where the whole screen would
    // otherwise hold nothing but a folded summary row.
    const { container } = render(
      <MessageGroup
        collapsedSteps
        isLoading
        messages={[
          {
            id: "ai-1",
            type: "ai",
            content: PREAMBLE,
            tool_calls: [
              { id: "call-1", name: "read_file", args: { path: "a.ts" } },
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
            content: "Here is what the file says.",
          } as Message,
        ]}
      />,
    );
    const text = container.textContent ?? "";

    expect(text).toContain("ran 1 steps");
    expect(text).not.toContain("ran 3 steps");
    expect(text).toContain(PREAMBLE);
    expect(text).toContain("Here is what the file says.");
    expect(text.indexOf(PREAMBLE)).toBeLessThan(
      text.indexOf("Here is what the file says."),
    );
    expect(collapsedSummary(container)?.getAttribute("aria-expanded")).toBe(
      "false",
    );
  });

  it("opens itself when an artifact step joins the group after mount", () => {
    const lsOnly = [
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [{ id: "call-1", name: "ls", args: { path: "." } }],
      } as Message,
    ];
    const { container, rerender } = render(
      <MessageGroup collapsedSteps messages={lsOnly} />,
    );

    expect(collapsedSummary(container)?.getAttribute("aria-expanded")).toBe(
      "false",
    );
    expect(container.textContent).not.toContain("draft.md");

    // The group keeps one instance for the whole turn (it is keyed by its
    // first AI message), so this is a plain re-render: a `write_file` arriving
    // after the `ls` must re-open it, or the new draft sits one tap deeper
    // than it did before the summary existed (T6 / F7-3).
    rerender(
      <MessageGroup
        collapsedSteps
        messages={[
          ...lsOnly,
          {
            id: "ai-2",
            type: "ai",
            content: "",
            tool_calls: [
              { id: "call-2", name: "write_file", args: { path: "draft.md" } },
            ],
          } as Message,
        ]}
      />,
    );

    expect(collapsedSummary(container)?.getAttribute("aria-expanded")).toBe(
      "true",
    );
    expect(container.textContent).toContain("draft.md");
  });

  it("treats every artifact tool name as an artifact row", () => {
    // `ARTIFACT_STEP_NAMES` is one list behind both the link row and the
    // auto-open — a name added there must not miss either one.
    for (const name of ["write_file", "str_replace"]) {
      const { container, unmount } = render(
        <MessageGroup
          collapsedSteps
          messages={[
            {
              id: `ai-${name}`,
              type: "ai",
              content: "",
              tool_calls: [
                { id: `call-${name}`, name, args: { path: `${name}.md` } },
              ],
            } as Message,
          ]}
        />,
      );

      expect(collapsedSummary(container)?.getAttribute("aria-expanded")).toBe(
        "true",
      );
      expect(container.textContent).toContain(`${name}.md`);
      unmount();
    }
  });

  it("leaves the desktop panel untouched when collapsedSteps is not passed", () => {
    const text = renderGroup(TWO_READS_AND_TEXT);

    // No summary row and no separate answer block: the dense panel only.
    expect(
      document.querySelector("[data-testid='mobile-collapsed-steps']"),
    ).toBeNull();
    expect(
      document.querySelector("[data-testid='mobile-collapsed-answer']"),
    ).toBeNull();
    expect(text).not.toContain("ran 2 steps");
    expect(document.querySelector("[aria-expanded]")).toBeNull();
    // Desktop keeps its own fold wording and paints the same rows as before:
    // the text above the last tool call, that tool call, and the fold.
    expect(text).toContain("2 more steps");
    expect(text).toContain(PREAMBLE);
    expect(text).toContain("b.ts");
    expect(text).not.toContain("a.ts");
  });

  it("collapses nothing for a reasoning-only turn", () => {
    // No tool call to summarise: the desktop panel is kept rather than
    // reporting "ran 1 steps · 0 tools".
    const { container } = render(
      <MessageGroup
        collapsedSteps
        isLoading
        messages={[
          {
            id: "ai-1",
            type: "ai",
            content: "",
            additional_kwargs: { reasoning_content: "I should search first." },
          } as Message,
        ]}
      />,
    );

    expect(container.textContent).not.toContain("ran 1 steps");
    expect(container.textContent).toContain("thinking");
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
