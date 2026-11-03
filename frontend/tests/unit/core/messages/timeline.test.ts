import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, test } from "@rstest/core";

import {
  buildTimelineEntries,
  type TimelineEntry,
} from "@/core/messages/timeline";
import type { PendingInterjection } from "@/core/threads/interjection";

function human(id: string, content: string): Message {
  return { id, type: "human", content } as Message;
}

function ai(
  id: string,
  content: string,
  extra: Partial<Record<string, unknown>> = {},
): Message {
  return { id, type: "ai", content, ...extra } as Message;
}

function tool(
  id: string,
  toolCallId: string,
  content: string,
  name = "web_search",
): Message {
  return {
    id,
    type: "tool",
    name,
    tool_call_id: toolCallId,
    content,
  } as Message;
}

function interjection(
  overrides: Partial<PendingInterjection> = {},
): PendingInterjection {
  return {
    id: "inter-1",
    thread_id: "t",
    run_id: "r1",
    text: "also check the tests",
    source: "web",
    seq: 1,
    created_at: "2026-11-03T00:00:00Z",
    applied: false,
    ...overrides,
  };
}

function kinds(entries: TimelineEntry[]) {
  return entries.map((entry) => entry.kind);
}

describe("buildTimelineEntries", () => {
  test("orders a multi-round run: human → steps → narration → answer → artifact", () => {
    const messages: Message[] = [
      human("h1", "research and write"),
      ai("a1", "", {
        additional_kwargs: { reasoning_content: "let me search first" },
        tool_calls: [{ id: "tc1", name: "web_search", args: { query: "q" } }],
      }),
      tool("t1", "tc1", "[]"),
      ai("a2", "mid-run status update", {
        additional_kwargs: { reasoning_content: "second round" },
        tool_calls: [
          { id: "tc2", name: "write_file", args: { path: "draft.md" } },
        ],
      }),
      tool("t2", "tc2", '"ok"'),
      ai("a3", "final answer"),
      ai("a4", "", {
        tool_calls: [
          {
            id: "tc3",
            name: "present_files",
            args: { filepaths: ["draft.md"] },
          },
        ],
      }),
    ];

    const entries = buildTimelineEntries(messages);

    expect(kinds(entries)).toEqual([
      "human",
      "step",
      "step",
      "step",
      "step",
      "step",
      "answer",
      "artifact",
    ]);
    // reasoning + narration + toolCall for the second-round message
    const steps = entries.filter(
      (entry): entry is Extract<TimelineEntry, { kind: "step" }> =>
        entry.kind === "step",
    );
    expect(steps.map((entry) => entry.step.variant)).toEqual([
      "reasoning",
      "toolCall",
      "reasoning",
      "narration",
      "toolCall",
    ]);
    // round index: first round = 1, second round = 2
    expect(steps.map((entry) => entry.roundIndex)).toEqual([1, 1, 2, 2, 2]);
    // tool results are attached and statuses settle
    expect(steps[1]?.step.status).toBe("done");
    expect(steps[1]?.step.result).toEqual([]);
    expect(steps[4]?.step.opensArtifact).toBe(true);
  });

  test("tool call without a result is running while loading, failed after", () => {
    const messages: Message[] = [
      human("h1", "go"),
      ai("a1", "", {
        tool_calls: [{ id: "tc1", name: "bash", args: { command: "ls" } }],
      }),
    ];
    const loading = buildTimelineEntries(messages, { isLoading: true });
    const stepLoading = (loading[1] as Extract<TimelineEntry, { kind: "step" }>)
      .step;
    expect(stepLoading.status).toBe("running");

    const settled = buildTimelineEntries(messages, { isLoading: false });
    const stepSettled = (settled[1] as Extract<TimelineEntry, { kind: "step" }>)
      .step;
    expect(stepSettled.status).toBe("failed");
  });

  test("#4304 — streaming content-only message is a streaming answer, then degrades to steps without losing content when tool_calls arrive", () => {
    const base: Message[] = [human("h1", "do the thing")];
    const streaming = ai("a1", "Working on it, let me check the files first");

    const before = buildTimelineEntries([...base, streaming], {
      isLoading: true,
    });
    expect(kinds(before)).toEqual(["human", "answer"]);
    const answer = before[1] as Extract<TimelineEntry, { kind: "answer" }>;
    expect(answer.streaming).toBe(true);
    expect(answer.message).toBe(streaming);

    const degraded: Message[] = [
      ...base,
      ai("a1", "Working on it, let me check the files first", {
        additional_kwargs: { reasoning_content: "need to look" },
        tool_calls: [{ id: "tc1", name: "ls", args: { path: "." } }],
      }),
    ];
    const after = buildTimelineEntries(degraded, { isLoading: true });
    expect(kinds(after)).toEqual(["human", "step", "step", "step"]);
    const narration = (after[2] as Extract<TimelineEntry, { kind: "step" }>)
      .step;
    expect(narration.variant).toBe("narration");
    // content survives the block→row downgrade
    expect(narration.content).toContain("Working on it");
  });

  test("#3868 — a settled content-only message renders once as an answer, its reasoning never as a step", () => {
    const messages: Message[] = [
      human("h1", "hi"),
      ai("a1", "the answer", {
        additional_kwargs: { reasoning_content: "private thoughts" },
      }),
    ];

    const entries = buildTimelineEntries(messages);
    expect(kinds(entries)).toEqual(["human", "answer"]);
    expect(
      entries.some(
        (entry) => entry.kind === "step" && entry.step.variant === "reasoning",
      ),
    ).toBe(false);
  });

  test("round counter resets at each new human turn", () => {
    const messages: Message[] = [
      human("h1", "first"),
      ai("a1", "", {
        tool_calls: [{ id: "tc1", name: "ls", args: {} }],
      }),
      ai("a2", "", {
        tool_calls: [{ id: "tc2", name: "ls", args: {} }],
      }),
      human("h2", "second"),
      ai("a3", "", {
        tool_calls: [{ id: "tc3", name: "ls", args: {} }],
      }),
    ];

    const entries = buildTimelineEntries(messages);
    const rounds = entries
      .filter(
        (entry): entry is Extract<TimelineEntry, { kind: "step" }> =>
          entry.kind === "step",
      )
      .map((entry) => entry.roundIndex);
    expect(rounds).toEqual([1, 2, 1]);
  });

  test("hidden messages produce no entries", () => {
    const messages: Message[] = [
      human("h1", "visible"),
      {
        id: "h2",
        type: "human",
        content: "secret answer",
        additional_kwargs: { hide_from_ui: true },
      } as Message,
      ai("a1", "done"),
    ];

    const entries = buildTimelineEntries(messages);
    expect(kinds(entries)).toEqual(["human", "answer"]);
  });

  test("clarification: reasoning stays a step, question becomes a bubble, tool message becomes a clarification artifact", () => {
    const messages: Message[] = [
      human("h1", "q"),
      ai("a1", "May I ask?", {
        additional_kwargs: { reasoning_content: "need info" },
        tool_calls: [{ id: "tc1", name: "ask_clarification", args: {} }],
      }),
      tool("t1", "tc1", "interrupted", "ask_clarification"),
    ];

    const entries = buildTimelineEntries(messages);
    expect(kinds(entries)).toEqual(["human", "step", "answer", "artifact"]);
    expect(
      (entries[3] as Extract<TimelineEntry, { kind: "artifact" }>).group.type,
    ).toBe("assistant:clarification");
  });

  test("task tool calls become a subagent artifact entry, not steps", () => {
    const messages: Message[] = [
      human("h1", "delegate"),
      ai("a1", "", {
        tool_calls: [
          {
            id: "tc1",
            name: "task",
            args: { subagent_type: "coder", description: "write it" },
          },
        ],
      }),
    ];

    const entries = buildTimelineEntries(messages);
    expect(kinds(entries)).toEqual(["human", "artifact"]);
    expect(
      (entries[1] as Extract<TimelineEntry, { kind: "artifact" }>).group.type,
    ).toBe("assistant:subagent");
  });
});

describe("interjection placement", () => {
  test("queued interjection renders at the timeline tail (applied=false, undoable)", () => {
    const messages: Message[] = [human("h1", "go"), ai("a1", "working…")];
    const entries = buildTimelineEntries(messages, {
      isLoading: true,
      interjections: [interjection()],
    });
    expect(kinds(entries)).toEqual(["human", "answer", "interjection"]);
    const queuedEvent = entries[2] as Extract<
      TimelineEntry,
      { kind: "interjection" }
    >;
    expect(queuedEvent.applied).toBe(false);
    expect(queuedEvent.text).toBe("also check the tests");
    expect(queuedEvent.landedMessage).toBeUndefined();
  });

  test("applied interjections always render as an event — once landed they merge with the HumanMessage at the injection position (no double bubble)", () => {
    const messages: Message[] = [
      human("h1", "go"),
      ai("a1", "", {
        tool_calls: [{ id: "tc1", name: "ls", args: {} }],
      }),
      human("h2", "also check the tests"),
      ai("a2", "on it"),
    ];
    const entries = buildTimelineEntries(messages, {
      isLoading: true,
      interjections: [interjection({ applied: true })],
    });
    // The interjection event takes over the landed HumanMessage's slot: one
    // event, no separate human bubble, position = injection timing.
    expect(kinds(entries)).toEqual(["human", "step", "interjection", "answer"]);
    const event = entries[2] as Extract<TimelineEntry, { kind: "interjection" }>;
    expect(event.applied).toBe(true);
    expect(event.text).toBe("also check the tests");
    expect(event.landedMessage?.id).toBe("h2");
    expect(
      entries.filter(
        (entry) => entry.kind === "human" && entry.message.id === "h2",
      ),
    ).toHaveLength(0);
  });

  test("applied but not yet landed — the event still renders (at the tail, its injection point) and never disappears", () => {
    const messages: Message[] = [
      human("h1", "go"),
      ai("a1", "step in progress"),
    ];
    const entries = buildTimelineEntries(messages, {
      isLoading: true,
      interjections: [interjection({ applied: true })],
    });
    expect(kinds(entries)).toEqual(["human", "answer", "interjection"]);
    const event = entries[2] as Extract<TimelineEntry, { kind: "interjection" }>;
    expect(event.applied).toBe(true);
    expect(event.landedMessage).toBeUndefined();
  });

  test("D4 — auto-run: the merged input renders as one interjection event (divider, no separate human bubble)", () => {
    const messages: Message[] = [
      human("h1", "first task"),
      ai("a1", "done"),
      human("h2", "merged follow-up text"),
      ai("a2", "continuing"),
    ];
    const entries = buildTimelineEntries(messages, {
      isLoading: true,
      interjections: [
        interjection({
          applied: false,
          auto_run_run_id: "r2",
          auto_run_input: "merged follow-up text",
        }),
      ],
    });
    expect(kinds(entries)).toEqual([
      "human",
      "answer",
      "interjection",
      "answer",
    ]);
    const event = entries[2] as Extract<TimelineEntry, { kind: "interjection" }>;
    expect(event.autoRunDivider).toBe(true);
    expect(event.text).toBe("merged follow-up text");
    expect(event.landedMessage?.id).toBe("h2");
    expect(
      entries.filter(
        (entry) => entry.kind === "human" && entry.message.id === "h2",
      ),
    ).toHaveLength(0);
  });

  test("D4 — auto-run bubble not yet landed renders behind a divider at the tail", () => {
    const messages: Message[] = [human("h1", "first"), ai("a1", "done")];
    const entries = buildTimelineEntries(messages, {
      isLoading: true,
      interjections: [
        interjection({
          auto_run_run_id: "r2",
          auto_run_input: "merged follow-up text",
        }),
      ],
    });
    expect(kinds(entries)).toEqual(["human", "answer", "interjection"]);
    expect(
      (entries[2] as Extract<TimelineEntry, { kind: "interjection" }>)
        .autoRunDivider,
    ).toBe(true);
  });

  test("multiple interjections keep seq order at the tail", () => {
    const messages: Message[] = [human("h1", "go"), ai("a1", "…")];
    const entries = buildTimelineEntries(messages, {
      isLoading: true,
      interjections: [
        interjection({ id: "b", seq: 2, text: "second" }),
        interjection({ id: "a", seq: 1, text: "first" }),
      ],
    });
    const texts = entries
      .filter(
        (entry): entry is Extract<TimelineEntry, { kind: "interjection" }> =>
          entry.kind === "interjection",
      )
      .map((entry) => entry.item.text);
    expect(texts).toEqual(["first", "second"]);
  });
});
