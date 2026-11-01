import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, test } from "@rstest/core";
import { renderHook } from "@testing-library/react";

import { useHumanInput } from "@/components/workspace/chats/use-human-input";

function toolMessageWithRequest(requestId: string): Message {
  return {
    id: `tool-${requestId}`,
    type: "tool",
    content: "fallback",
    artifact: {
      human_input: {
        version: 1,
        kind: "human_input_request",
        source: "ask_clarification",
        request_id: requestId,
        question: "Which env?",
        input_mode: "free_text",
      },
    },
  } as unknown as Message;
}

function hiddenAnswer(requestId: string): Message {
  return {
    id: `human-${requestId}`,
    type: "human",
    content: "answer",
    additional_kwargs: {
      hide_from_ui: true,
      human_input_response: {
        version: 1,
        kind: "human_input_response",
        source: "ask_clarification",
        request_id: requestId,
        response_kind: "text",
        value: "dev",
      },
    },
  } as unknown as Message;
}

function withState<T>(hook: () => T): { current: T } {
  const utils = renderHook(hook);
  return { current: utils.result.current };
}

describe("useHumanInput", () => {
  const noopSend = async () => undefined;

  test("no open card without messages", () => {
    const { current } = withState(() =>
      useHumanInput({
        threadId: "thread-1",
        sendMessage: noopSend,
        messages: [],
      }),
    );
    expect(current.hasOpenHumanInputCard).toBe(false);
  });

  test("open request flags the card; disabled hook does not", () => {
    const messages = [toolMessageWithRequest("clarification:call-1")];

    const enabled = withState(() =>
      useHumanInput({
        threadId: "thread-1",
        sendMessage: noopSend,
        messages,
      }),
    );
    expect(enabled.current.hasOpenHumanInputCard).toBe(true);

    const gated = withState(() =>
      useHumanInput({
        threadId: "thread-1",
        sendMessage: noopSend,
        messages,
        enabled: false,
      }),
    );
    expect(gated.current.hasOpenHumanInputCard).toBe(false);
  });

  test("structured answer closes the card", () => {
    const { current } = withState(() =>
      useHumanInput({
        threadId: "thread-1",
        sendMessage: noopSend,
        messages: [
          toolMessageWithRequest("clarification:call-1"),
          hiddenAnswer("clarification:call-1"),
        ],
      }),
    );
    expect(current.hasOpenHumanInputCard).toBe(false);
  });

  test("submit sends a hidden human message with the response metadata", async () => {
    const calls: Array<{
      threadId: string;
      text: string;
      extraContext?: Record<string, unknown>;
      additionalKwargs?: Record<string, unknown>;
    }> = [];
    const sendMessage = async (
      threadId: string,
      message: { text: string },
      extraContext?: Record<string, unknown>,
      options?: { additionalKwargs?: Record<string, unknown> },
    ) => {
      calls.push({
        threadId,
        text: message.text,
        extraContext,
        additionalKwargs: options?.additionalKwargs,
      });
    };

    const { current } = withState(() =>
      useHumanInput({
        threadId: "thread-1",
        sendMessage,
        messages: [toolMessageWithRequest("clarification:call-1")],
        extraContext: { agent_name: "researcher" },
      }),
    );

    const sent = await current.handleSubmitHumanInput(
      {
        version: 1,
        kind: "human_input_request",
        source: "ask_clarification",
        request_id: "clarification:call-1",
        question: "Which env?",
        input_mode: "free_text",
      },
      {
        version: 1,
        kind: "human_input_response",
        source: "ask_clarification",
        request_id: "clarification:call-1",
        response_kind: "option",
        option_id: "option-1",
        value: "dev",
      },
    );

    expect(sent).toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.threadId).toBe("thread-1");
    expect(calls[0]!.text).toBe(
      'For your clarification "Which env?", my answer is: dev',
    );
    expect(calls[0]!.extraContext).toEqual({ agent_name: "researcher" });
    expect(calls[0]!.additionalKwargs).toEqual({
      hide_from_ui: true,
      human_input_response: {
        version: 1,
        kind: "human_input_response",
        source: "ask_clarification",
        request_id: "clarification:call-1",
        response_kind: "option",
        option_id: "option-1",
        value: "dev",
      },
    });
  });

  test("send failure propagates to the caller", async () => {
    const { current } = withState(() =>
      useHumanInput({
        threadId: "thread-1",
        sendMessage: async () => {
          throw new Error("network down");
        },
        messages: [toolMessageWithRequest("clarification:call-1")],
      }),
    );

    await expect(
      current.handleSubmitHumanInput(
        {
          version: 1,
          kind: "human_input_request",
          source: "ask_clarification",
          request_id: "clarification:call-1",
          question: "Which env?",
          input_mode: "free_text",
        },
        {
          version: 1,
          kind: "human_input_response",
          source: "ask_clarification",
          request_id: "clarification:call-1",
          response_kind: "text",
          value: "dev",
        },
      ),
    ).rejects.toThrow("network down");
  });
});
