import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, test } from "@rstest/core";

import {
  extractContentFromMessage,
  extractTextFromMessage,
  extractReasoningContentFromMessage,
  getAssistantTurnCopyData,
  getAssistantTurnUsageMessages,
  getMessageGroups,
  getStreamingMessageLookup,
  hasAssistantTextInCurrentTurn,
  hasContent,
  hasReasoning,
  isAssistantMessageGroupStreaming,
  isHiddenFromUIMessage,
  stripUploadedFilesTag,
} from "@/core/messages/utils";

function aiMessage(content: string): Message {
  return {
    id: "ai-1",
    type: "ai",
    content,
  } as Message;
}

test("aggregates token usage messages once per assistant turn", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Plan a trip",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [{ id: "tool-1", name: "web_search", args: {} }],
      usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
    },
    {
      id: "tool-1-result",
      type: "tool",
      name: "web_search",
      tool_call_id: "tool-1",
      content: "[]",
    },
    {
      id: "ai-2",
      type: "ai",
      content: "Here is the itinerary",
      usage_metadata: { input_tokens: 2, output_tokens: 8, total_tokens: 10 },
    },
    {
      id: "human-2",
      type: "human",
      content: "Make it shorter",
    },
    {
      id: "ai-3",
      type: "ai",
      content: "Short version",
      usage_metadata: { input_tokens: 1, output_tokens: 1, total_tokens: 2 },
    },
  ] as Message[];

  const groups = getMessageGroups(messages);
  const usageMessagesByGroupIndex = getAssistantTurnUsageMessages(groups);

  expect(groups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
    "assistant",
    "human",
    "assistant",
  ]);

  expect(
    usageMessagesByGroupIndex.map(
      (groupMessages) => groupMessages?.map((message) => message.id) ?? null,
    ),
  ).toEqual([null, null, ["ai-1", "ai-2"], null, ["ai-3"]]);
});

test("reasoning + content (no tool calls) yields a single assistant bubble, not a duplicate processing group", () => {
  // Regression #3868: reasoning + answer must render exactly once.
  const messages = [
    { id: "human-1", type: "human", content: "Why is the sky blue?" },
    {
      id: "ai-1",
      type: "ai",
      content: "Rayleigh scattering makes the sky blue.",
      additional_kwargs: { reasoning_content: "Recall Rayleigh scattering." },
    },
  ] as Message[];

  const groups = getMessageGroups(messages);

  expect(groups.map((group) => group.type)).toEqual(["human", "assistant"]);

  const turnUsage = getAssistantTurnUsageMessages(groups);
  expect(turnUsage.at(-1)?.map((message) => message.id)).toEqual(["ai-1"]);
});

test("keeps unresolved streaming text in the processing group when tool calls arrive later", () => {
  // Regression #4304: pre-tool text stays in the processing group while loading.
  const textOnlyMessages = [
    { id: "human-1", type: "human", content: "Create a presentation" },
    {
      id: "ai-1",
      type: "ai",
      content: "I will inspect the source material first.",
    },
  ] as Message[];

  const textOnlyGroups = getMessageGroups(textOnlyMessages, {
    isCurrentTurnLoading: true,
  });
  expect(textOnlyGroups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
  ]);

  const withToolCall = [
    textOnlyMessages[0],
    {
      ...textOnlyMessages[1],
      tool_calls: [
        { id: "call-1", name: "read_file", args: { path: "slides.md" } },
      ],
    },
  ] as Message[];
  const toolCallGroups = getMessageGroups(withToolCall, {
    isCurrentTurnLoading: true,
  });
  expect(toolCallGroups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
  ]);
  // Same group id across the transition, so React keeps the container.
  expect(toolCallGroups[1]?.id).toBe(textOnlyGroups[1]?.id);

  expect(getMessageGroups(textOnlyMessages).map((group) => group.type)).toEqual(
    ["human", "assistant"],
  );
});

test("keeps post-tool streaming text in the processing group until the turn settles", () => {
  const messages = [
    { id: "human-1", type: "human", content: "Inspect and summarize" },
    {
      id: "ai-1",
      type: "ai",
      content: "I will inspect the current implementation.",
      tool_calls: [
        { id: "call-1", name: "read_file", args: { path: "source.ts" } },
      ],
    },
    {
      id: "tool-1",
      type: "tool",
      name: "read_file",
      tool_call_id: "call-1",
      content: "file contents",
    },
    {
      id: "ai-2",
      type: "ai",
      content: "Here is the final streamed answer.",
    },
  ] as Message[];

  const loadingGroups = getMessageGroups(messages, {
    isCurrentTurnLoading: true,
  });
  expect(loadingGroups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
  ]);
  expect(loadingGroups[1]?.messages.map((message) => message.id)).toEqual([
    "ai-1",
    "tool-1",
    "ai-2",
  ]);

  expect(getMessageGroups(messages).map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
    "assistant",
  ]);
});

test("keeps clarification bubbles while the current turn is loading", () => {
  // Clarification messages are never unresolved text (#4304 exemption).
  const messages = [
    { id: "human-1", type: "human", content: "Plan my trip" },
    {
      id: "ai-1",
      type: "ai",
      content: "Before I continue, which city should I plan for?",
      tool_calls: [
        {
          id: "call-1",
          name: "ask_clarification",
          args: { question: "Which city?" },
        },
      ],
    },
  ] as Message[];

  for (const isCurrentTurnLoading of [true, false]) {
    const groups = getMessageGroups(messages, { isCurrentTurnLoading });
    expect(groups.map((group) => group.type)).toEqual([
      "human",
      "assistant:processing",
      "assistant",
    ]);
  }
});

describe("inline <think> tag splitting", () => {
  test("strips a fully closed <think> block from AI content", () => {
    const message = aiMessage("<think>internal reasoning</think>final answer");
    expect(extractContentFromMessage(message)).toBe("final answer");
    expect(extractReasoningContentFromMessage(message)).toBe(
      "internal reasoning",
    );
  });

  test("strips multiple closed <think> blocks and joins their reasoning", () => {
    const message = aiMessage(
      "<think>step one</think>between<think>step two</think>after",
    );
    expect(extractContentFromMessage(message)).toBe("betweenafter");
    expect(extractReasoningContentFromMessage(message)).toBe(
      "step one\n\nstep two",
    );
  });

  test("during streaming, an unclosed <think> tag does not leak its tail into content", () => {
    // Simulates accumulated content mid-stream, before </think> arrives.
    const message = aiMessage(
      "<think>I need to analyze the user's question step by",
    );
    expect(extractContentFromMessage(message)).toBe("");
    expect(extractContentFromMessage(message)).not.toContain("<think>");
    expect(extractReasoningContentFromMessage(message)).toBe(
      "I need to analyze the user's question step by",
    );
  });

  test("preamble before an unclosed <think> stays in content", () => {
    const message = aiMessage(
      "Here is part of the answer.<think>but wait, let me reconsider",
    );
    expect(extractContentFromMessage(message)).toBe(
      "Here is part of the answer.",
    );
    expect(extractReasoningContentFromMessage(message)).toBe(
      "but wait, let me reconsider",
    );
  });

  test("closed <think> followed by a trailing unclosed <think> merges both into reasoning", () => {
    const message = aiMessage(
      "<think>first step</think>partial answer<think>second step still streaming",
    );
    expect(extractContentFromMessage(message)).toBe("partial answer");
    expect(extractReasoningContentFromMessage(message)).toBe(
      "first step\n\nsecond step still streaming",
    );
  });

  test("hasReasoning recognises an unclosed <think> tag mid-stream", () => {
    expect(hasReasoning(aiMessage("<think>thinking in progress"))).toBe(true);
  });

  test("hasContent excludes an unclosed <think> tail when no preamble exists", () => {
    expect(hasContent(aiMessage("<think>thinking in progress"))).toBe(false);
  });

  test("hasContent stays true when preamble precedes an unclosed <think>", () => {
    expect(hasContent(aiMessage("preamble<think>still thinking"))).toBe(true);
  });

  test("a lone <think> open tag with no body yields no reasoning and no content", () => {
    const message = aiMessage("<think>");
    expect(extractContentFromMessage(message)).toBe("");
    expect(extractReasoningContentFromMessage(message)).toBeNull();
    expect(hasReasoning(message)).toBe(false);
  });

  test("a literal <think> inside markdown inline code is not treated as reasoning", () => {
    const message = aiMessage(
      "Use `<think>` markers to delimit reasoning sections.",
    );
    expect(extractContentFromMessage(message)).toBe(
      "Use `<think>` markers to delimit reasoning sections.",
    );
    expect(extractReasoningContentFromMessage(message)).toBeNull();
    expect(hasReasoning(message)).toBe(false);
  });

  test("a backtick-prefixed <think> mid-stream is not split into reasoning", () => {
    // Simulates the moment the model has emitted the opening backtick and
    // `<think>` for a literal documentation reference, before the closing
    // backtick arrives. The pre-fix behaviour would have permanently
    // truncated the content here.
    const message = aiMessage("Documentation: `<think>");
    expect(extractContentFromMessage(message)).toBe("Documentation: `<think>");
    expect(extractReasoningContentFromMessage(message)).toBeNull();
  });

  test("re-splits when the same message object gets new content", () => {
    // Streaming replaces `content` on the same object, so the split cache is
    // keyed by the content it was derived from.
    const message = aiMessage("<think>first</think>one");

    expect(extractContentFromMessage(message)).toBe("one");
    expect(extractReasoningContentFromMessage(message)).toBe("first");

    (message as { content: string }).content = "<think>second</think>two";

    expect(extractContentFromMessage(message)).toBe("two");
    expect(extractReasoningContentFromMessage(message)).toBe("second");
    expect(hasReasoning(message)).toBe(true);
  });
});

describe("isHiddenFromUIMessage", () => {
  function contentReadCounter(
    message: Omit<Message, "content">,
    content: string,
  ) {
    const counter = { reads: 0 };
    const probe = {
      ...message,
      get content() {
        counter.reads++;
        return content;
      },
    } as unknown as Message;
    return { probe, counter };
  }

  test("does not read AI content to decide visibility", () => {
    // Reading AI content here would cost a full scan per message per chunk.
    const { probe, counter } = contentReadCounter(
      { id: "ai-visible", type: "ai" } as Message,
      "<think>long reasoning</think>a long streamed answer",
    );

    expect(isHiddenFromUIMessage(probe)).toBe(false);
    expect(counter.reads).toBe(0);
  });

  test("does not read content when a control message name already hides it", () => {
    const { probe, counter } = contentReadCounter(
      { id: "summary-1", type: "ai", name: "summary" } as Message,
      "compressed context",
    );

    expect(isHiddenFromUIMessage(probe)).toBe(true);
    expect(counter.reads).toBe(0);
  });

  test("still hides a human message that is only slash skill activation", () => {
    const message = {
      id: "slash-activation",
      type: "human",
      content:
        "<slash_skill_activation>\n<skill_content># SKILL.md</skill_content>\n</slash_skill_activation>",
    } as Message;

    expect(isHiddenFromUIMessage(message)).toBe(true);
  });

  test("keeps a human message that carries real text alongside the activation", () => {
    const message = {
      id: "slash-activation-with-task",
      type: "human",
      content:
        "<slash_skill_activation>\n<skill_content># SKILL.md</skill_content>\n</slash_skill_activation>\nreal user task",
    } as Message;

    expect(isHiddenFromUIMessage(message)).toBe(false);
  });

  test("hides any message flagged with hide_from_ui", () => {
    const message = {
      id: "hidden-1",
      type: "human",
      content: "internal reply",
      additional_kwargs: { hide_from_ui: true },
    } as Message;

    expect(isHiddenFromUIMessage(message)).toBe(true);
  });
});

describe("human message internal context stripping", () => {
  test("strips slash skill activation context from display content", () => {
    const content =
      "<slash_skill_activation>\n<skill_content># Secret SKILL.md</skill_content>\n</slash_skill_activation>\nreal user task";

    expect(stripUploadedFilesTag(content)).toBe("real user task");
  });

  test("hides leaked slash skill activation messages with no user text", () => {
    const messages = [
      {
        id: "slash-activation",
        type: "human",
        content:
          "<slash_skill_activation>\n<skill_content># Secret SKILL.md</skill_content>\n</slash_skill_activation>",
      },
      {
        id: "ai-1",
        type: "ai",
        content: "Public answer",
      },
    ] as Message[];

    const groups = getMessageGroups(messages);

    expect(groups.map((group) => group.type)).toEqual(["assistant"]);
    expect(
      groups.flatMap((group) => group.messages).map((message) => message.id),
    ).toEqual(["ai-1"]);
  });
});

test("hides internal todo reminder messages from message groups", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Audit the middleware",
    },
    {
      id: "todo-reminder-1",
      type: "human",
      name: "todo_completion_reminder",
      content: "<system_reminder>finish todos</system_reminder>",
    },
    {
      id: "todo-reminder-2",
      type: "human",
      name: "todo_reminder",
      content: "<system_reminder>remember todos</system_reminder>",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Done",
    },
  ] as Message[];

  const groups = getMessageGroups(messages);

  expect(groups.map((group) => group.type)).toEqual(["human", "assistant"]);
  expect(
    groups.flatMap((group) => group.messages).map((message) => message.id),
  ).toEqual(["human-1", "ai-1"]);
});

test("hides assistant copy data while that turn is streaming", () => {
  const messages = [
    {
      id: "ai-1",
      type: "ai",
      content: "Partial answer",
    },
  ] as Message[];

  expect(getAssistantTurnCopyData(messages)).toBe("Partial answer");
  expect(getAssistantTurnCopyData(messages, { isStreaming: true })).toBeNull();
});

test("marks the latest assistant message as streaming", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Still generating",
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true, () => ({
        streamMetadata: { langgraph_node: "agent" },
      })),
    ),
  ).toBe(true);
  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, false, () => ({
        streamMetadata: { langgraph_node: "agent" },
      })),
    ),
  ).toBe(false);
});

test("keeps previous assistant copyable while waiting for a new visible answer", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Completed answer",
    },
    {
      id: "opt-human-1",
      type: "human",
      content: "Continue",
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true),
    ),
  ).toBe(false);
});

test("keeps previous assistant copyable while a hidden send is starting", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Completed answer",
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true),
    ),
  ).toBe(false);
});

test("keeps previous assistant copyable after a hidden send is appended", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Completed answer",
    },
    {
      id: "human-hidden",
      type: "human",
      content: "Save this agent",
      additional_kwargs: { hide_from_ui: true },
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true),
    ),
  ).toBe(false);
});

test("falls back to reasoning for a reasoning-only assistant turn's copy data", () => {
  // A turn can end with reasoning but no answer text (e.g. stopped during
  // thinking); the turn-level copy button must not disappear in that case.
  const messages = [
    {
      id: "ai-1",
      type: "ai",
      content: "",
      additional_kwargs: { reasoning_content: "the actual reasoning" },
    },
  ] as Message[];

  expect(getAssistantTurnCopyData(messages)).toBe("the actual reasoning");
});

test("settled copy data is derived once per messages array reference (#5094)", () => {
  // The cache is keyed on the messages array reference, so any caller that
  // reads copy data for the same settled array twice is served from the cache
  // instead of re-running the O(turn bytes) extraction. Reading `content`
  // through a getter proves the second call never touches the message.
  let contentReads = 0;
  const message = {
    id: "ai-1",
    type: "ai",
    get content() {
      contentReads += 1;
      return "Final answer";
    },
  } as unknown as Message;
  const messages = [message];

  expect(getAssistantTurnCopyData(messages)).toBe("Final answer");
  const readsAfterFirstCall = contentReads;
  expect(readsAfterFirstCall).toBeGreaterThan(0);

  expect(getAssistantTurnCopyData(messages)).toBe("Final answer");
  expect(contentReads).toBe(readsAfterFirstCall);
});

test("copy-data cache does not leak across array references", () => {
  const first = [
    { id: "ai-1", type: "ai", content: "first answer" },
  ] as Message[];
  const second = [
    { id: "ai-2", type: "ai", content: "second answer" },
  ] as Message[];

  expect(getAssistantTurnCopyData(first)).toBe("first answer");
  expect(getAssistantTurnCopyData(second)).toBe("second answer");
  // The streaming short-circuit stays ahead of the cache.
  expect(getAssistantTurnCopyData(second, { isStreaming: true })).toBeNull();
  expect(getAssistantTurnCopyData(second)).toBe("second answer");
});

test("null copy data is not cached for a reference", () => {
  // A turn with no copyable AI text must keep recomputing (and stay null)
  // rather than a cached null hiding a later value — the same array can be
  // re-used once messages are appended to a rebuilt group.
  const messages = [
    { id: "human-1", type: "human", content: "hi" },
  ] as Message[];

  expect(getAssistantTurnCopyData(messages)).toBeNull();
  expect(getAssistantTurnCopyData(messages)).toBeNull();
});

test("uses stream metadata to identify an assistant before optimistic input", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Completed answer",
    },
    {
      id: "ai-2",
      type: "ai",
      content: "Still generating",
    },
    {
      id: "opt-human-1",
      type: "human",
      content: "Continue",
    },
  ] as Message[];
  const assistantGroups = getMessageGroups(messages).filter(
    (group) => group.type === "assistant",
  );
  const groups = getMessageGroups(messages);
  const assistantGroupIndexes = groups
    .map((group, index) => (group.type === "assistant" ? index : -1))
    .filter((index) => index >= 0);

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndexes[0] ?? -1]?.messages ?? [],
      getStreamingMessageLookup(messages, true, (message) =>
        message.id === "ai-2"
          ? { streamMetadata: { langgraph_node: "agent" } }
          : undefined,
      ),
    ),
  ).toBe(false);
  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndexes[1] ?? -1]?.messages ?? [],
      getStreamingMessageLookup(messages, true, (message) =>
        message.id === "ai-2"
          ? { streamMetadata: { langgraph_node: "agent" } }
          : undefined,
      ),
    ),
  ).toBe(true);
  expect(assistantGroups.map((group) => group.id)).toEqual(["ai-1", "ai-2"]);
});

test("does not mark a completed assistant group streaming from a later processing group", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Visible answer",
    },
    {
      id: "ai-2",
      type: "ai",
      content: "",
      tool_calls: [{ id: "tool-1", name: "web_search", args: {} }],
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(groups.map((group) => group.type)).toEqual([
    "human",
    "assistant",
    "assistant:processing",
  ]);
  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true, (message) =>
        message.id === "ai-2"
          ? { streamMetadata: { langgraph_node: "agent" } }
          : undefined,
      ),
    ),
  ).toBe(false);
});

test("keeps streaming assistant hidden when a hidden control message follows it", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Still generating",
    },
    {
      id: "human-hidden",
      type: "human",
      content: "Save this agent",
      additional_kwargs: { hide_from_ui: true },
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true, (message) =>
        message.id === "ai-1"
          ? { streamMetadata: { langgraph_node: "agent" } }
          : undefined,
      ),
    ),
  ).toBe(true);
});

describe("multi-part content with bare-string continuations", () => {
  // Gemini streams the first content block as a {type:"text"} object carrying
  // the thinking signature, then emits continuation deltas as plain strings.
  // LangChain's Python merge_content preserves these as bare-string elements,
  // so the finalized message content is [{type:"text", ...}, "...rest..."].
  const geminiMessage = {
    id: "ai-1",
    type: "ai",
    content: [
      {
        type: "text",
        text: "First block carrying the signature.",
        extras: { signature: "abc123" },
        index: 0,
      },
      "Continuation streamed as a bare string.",
    ],
  } as unknown as Message;

  test("extractContentFromMessage includes the bare-string parts", () => {
    expect(extractContentFromMessage(geminiMessage)).toBe(
      "First block carrying the signature.\nContinuation streamed as a bare string.",
    );
  });

  test("extractTextFromMessage includes the bare-string parts", () => {
    expect(extractTextFromMessage(geminiMessage)).toBe(
      "First block carrying the signature.\nContinuation streamed as a bare string.",
    );
  });
});

describe("hasAssistantTextInCurrentTurn", () => {
  // A clarification turn: preamble text + the ask_clarification tool call.
  const clarificationTranscript = [
    { id: "human-1", type: "human", content: "把动作改成激光攻击" },
    {
      id: "ai-1",
      type: "ai",
      content: "已把动作改为激光攻击，是否按此继续？",
      tool_calls: [
        {
          id: "call-1",
          name: "ask_clarification",
          args: { question: "是否按此继续？" },
        },
      ],
    },
    {
      id: "tool-1",
      type: "tool",
      name: "ask_clarification",
      tool_call_id: "call-1",
      content: "fallback text",
    },
  ] as Message[];

  // The answer the user submits is a hidden human message: it starts a new
  // turn but never forms a group of its own.
  const hiddenAnswer = {
    id: "human-answer",
    type: "human",
    content: 'For your clarification "是否按此继续？", my answer is: 继续',
    additional_kwargs: {
      hide_from_ui: true,
      human_input_response: {
        version: 1,
        kind: "human_input_response",
        source: "ask_clarification",
        request_id: "clarification:call-1",
        response_kind: "text",
        value: "继续",
      },
    },
  } as Message;

  test("ignores the previous turn's clarification text once the answer is submitted", () => {
    const messages = [
      ...clarificationTranscript,
      hiddenAnswer,
      { id: "ai-2", type: "ai", content: "" },
    ] as Message[];
    const groups = getMessageGroups(messages, { isCurrentTurnLoading: true });

    expect(hasAssistantTextInCurrentTurn(messages, groups)).toBe(false);
  });

  test("counts text streamed after the answer", () => {
    const messages = [
      ...clarificationTranscript,
      hiddenAnswer,
      { id: "ai-2", type: "ai", content: "好的，开始生成计划。" },
    ] as Message[];
    const groups = getMessageGroups(messages, { isCurrentTurnLoading: true });

    expect(hasAssistantTextInCurrentTurn(messages, groups)).toBe(true);
  });

  test("counts a settled assistant bubble of the current turn", () => {
    const messages = [
      ...clarificationTranscript,
      hiddenAnswer,
      { id: "ai-2", type: "ai", content: "好的，开始生成计划。" },
    ] as Message[];
    const groups = getMessageGroups(messages);

    expect(hasAssistantTextInCurrentTurn(messages, groups)).toBe(true);
  });

  test("ignores assistant text from before the last human message", () => {
    const messages = [
      { id: "human-1", type: "human", content: "hi" },
      { id: "ai-1", type: "ai", content: "Hello!" },
      { id: "human-2", type: "human", content: "again" },
      { id: "ai-2", type: "ai", content: "" },
    ] as Message[];
    const groups = getMessageGroups(messages);

    expect(hasAssistantTextInCurrentTurn(messages, groups)).toBe(false);
  });
});
