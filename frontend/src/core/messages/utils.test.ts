import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it, vi } from "vitest";

import { groupMessages } from "./utils";

describe("groupMessages", () => {
  it("attaches tool messages to the group registered by tool_call_id", () => {
    const aiWithToolCall = {
      type: "ai",
      id: "ai-1",
      content: "",
      tool_calls: [{ id: "call-1", name: "bash", args: {} }],
    } as Message;
    const assistantBubble = {
      type: "ai",
      id: "ai-2",
      content: "intermediate text",
    } as Message;
    const toolResult = {
      type: "tool",
      id: "tool-1",
      content: "ok",
      name: "bash",
      tool_call_id: "call-1",
    } as Message;

    const groups = groupMessages(
      [aiWithToolCall, assistantBubble, toolResult],
      (group) => group,
    );

    expect(groups).toHaveLength(2);
    expect(groups[0]?.type).toBe("assistant:processing");
    expect(groups[0]?.messages).toEqual([aiWithToolCall, toolResult]);
    expect(groups[1]?.type).toBe("assistant");
    expect(groups[1]?.messages).toEqual([assistantBubble]);
  });

  it("silently skips unmatched orphan tool messages", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const toolResult = {
      type: "tool",
      id: "tool-1",
      content: "orphan",
      name: "bash",
      tool_call_id: "missing-call",
    } as Message;

    const groups = groupMessages([toolResult], (group) => group);

    expect(groups).toEqual([]);
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("skips messages marked as hidden from the UI", () => {
    const hiddenImageContext = {
      type: "human",
      id: "hidden-1",
      content: "Here are the images you've viewed:",
      additional_kwargs: { hide_from_ui: true },
    } as Message;
    const visibleUserMessage = {
      type: "human",
      id: "human-1",
      content: "Please inspect the image.",
    } as Message;

    const groups = groupMessages(
      [hiddenImageContext, visibleUserMessage],
      (group) => group,
    );

    expect(groups).toHaveLength(1);
    expect(groups[0]?.messages).toEqual([visibleUserMessage]);
  });

  it("shows assistant text before an ask_clarification result", () => {
    const aiWithClarification = {
      type: "ai",
      id: "ai-clarification",
      content: [
        {
          type: "text",
          text: "Here is the analysis before I ask for confirmation.",
        },
        {
          type: "tool_use",
          id: "call-clarification",
          name: "ask_clarification",
          input: {},
        },
      ],
      tool_calls: [
        {
          id: "call-clarification",
          name: "ask_clarification",
          args: {
            question: "Can I proceed?",
          },
        },
      ],
    } as unknown as Message;
    const clarificationResult = {
      type: "tool",
      id: "tool-clarification",
      content: "Can I proceed?",
      name: "ask_clarification",
      tool_call_id: "call-clarification",
    } as Message;

    const groups = groupMessages(
      [aiWithClarification, clarificationResult],
      (group) => group,
    );

    expect(groups.map((group) => group.type)).toEqual([
      "assistant:processing",
      "assistant",
      "assistant:clarification",
    ]);
    expect(groups[0]?.messages).toEqual([
      aiWithClarification,
      clarificationResult,
    ]);
    expect(groups[1]?.messages).toEqual([aiWithClarification]);
    expect(groups[2]?.messages).toEqual([clarificationResult]);
  });

  it("does not show an assistant bubble for empty ask_clarification content", () => {
    const aiWithClarification = {
      type: "ai",
      id: "ai-clarification",
      content: [
        {
          type: "tool_use",
          id: "call-clarification",
          name: "ask_clarification",
          input: {},
        },
      ],
      tool_calls: [
        {
          id: "call-clarification",
          name: "ask_clarification",
          args: {
            question: "Can I proceed?",
          },
        },
      ],
    } as unknown as Message;
    const clarificationResult = {
      type: "tool",
      id: "tool-clarification",
      content: "Can I proceed?",
      name: "ask_clarification",
      tool_call_id: "call-clarification",
    } as Message;

    const groups = groupMessages(
      [aiWithClarification, clarificationResult],
      (group) => group,
    );

    expect(groups.map((group) => group.type)).toEqual([
      "assistant:processing",
      "assistant:clarification",
    ]);
  });
});
