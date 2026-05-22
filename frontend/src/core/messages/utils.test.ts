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
});
