import type { Message } from "@langchain/langgraph-sdk";
import { expect, test } from "vitest";

import {
  buildRunMessagesUrl,
  buildThreadMessagesUrl,
  getNextRunMessagesBeforeSeq,
  getOldestRunMessageSeq,
  getSummarizationMiddlewareMessages,
  getVisibleOptimisticMessages,
  mergeHistoryLiveMessages,
  mergeMessages,
  mergeRunMessageRows,
  runEventRowsToMessages,
  runMessagesPageHasMore,
} from "@/core/threads/hooks";
import type { RunMessage } from "@/core/threads/types";

function runMessage(seq?: number): RunMessage {
  return {
    run_id: "run-1",
    ...(seq === undefined ? {} : { seq }),
    content: {} as Message,
    metadata: { caller: "" },
    created_at: "2026-05-22T00:00:00Z",
  };
}

test("mergeMessages removes duplicate messages already present in history", () => {
  const human = {
    id: "human-1",
    type: "human",
    content: "Design an agent",
  } as Message;
  const ai = {
    id: "ai-1",
    type: "ai",
    content: "Let's design it.",
  } as Message;

  expect(mergeMessages([human, ai, human, ai], [], [])).toEqual([human, ai]);
});

test("mergeMessages lets live thread messages replace overlapping history", () => {
  const oldHuman = {
    id: "human-1",
    type: "human",
    content: "old",
  } as Message;
  const liveHuman = {
    id: "human-1",
    type: "human",
    content: "live",
  } as Message;
  const oldAi = {
    id: "ai-1",
    type: "ai",
    content: "old",
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "live",
  } as Message;

  expect(mergeMessages([oldHuman, oldAi], [liveHuman, liveAi], [])).toEqual([
    liveHuman,
    liveAi,
  ]);
});

test("mergeMessages deduplicates tool messages by tool_call_id", () => {
  const oldTool = {
    id: "tool-message-old",
    type: "tool",
    tool_call_id: "call-1",
    content: "old",
  } as Message;
  const liveTool = {
    id: "tool-message-live",
    type: "tool",
    tool_call_id: "call-1",
    content: "live",
  } as Message;

  expect(mergeMessages([oldTool], [liveTool], [])).toEqual([liveTool]);
});

test("mergeMessages keeps a visible history message when a hidden live message reuses its id", () => {
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "visible user prompt",
  } as Message;
  const hiddenReminder = {
    id: "human-1",
    type: "human",
    content: "<system-reminder>hidden</system-reminder>",
    additional_kwargs: { hide_from_ui: true },
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "live answer",
  } as Message;

  expect(mergeMessages([historyHuman], [hiddenReminder, liveAi], [])).toEqual([
    historyHuman,
    liveAi,
  ]);
});

test("mergeMessages lets a visible live message replace overlapping hidden history", () => {
  const hiddenHistoryHuman = {
    id: "human-1",
    type: "human",
    content: "<system-reminder>hidden</system-reminder>",
    additional_kwargs: { hide_from_ui: true },
  } as Message;
  const liveHuman = {
    id: "human-1",
    type: "human",
    content: "visible user prompt",
  } as Message;

  expect(mergeMessages([hiddenHistoryHuman], [liveHuman], [])).toEqual([
    liveHuman,
  ]);
});

test("getSummarizationMiddlewareMessages matches DeerFlow summarization update keys", () => {
  const removeAll = {
    id: "__remove_all__",
    type: "remove",
    content: "",
  } as Message;
  const summary = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "summary",
  } as Message;

  expect(
    getSummarizationMiddlewareMessages({
      "DeerFlowSummarizationMiddleware.before_model": {
        messages: [removeAll, summary],
      },
    }),
  ).toEqual([removeAll, summary]);
});

test("getSummarizationMiddlewareMessages matches base LangChain summarization update keys", () => {
  const summary = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "summary",
  } as Message;

  expect(
    getSummarizationMiddlewareMessages({
      "SummarizationMiddleware.before_model": {
        messages: [summary],
      },
    }),
  ).toEqual([summary]);
});

test("getSummarizationMiddlewareMessages ignores unrelated suffix-sharing update keys", () => {
  const summary = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "summary",
  } as Message;

  expect(
    getSummarizationMiddlewareMessages({
      "OtherSummarizationMiddleware.before_model": {
        messages: [summary],
      },
    }),
  ).toBeUndefined();
});

test("getVisibleOptimisticMessages hides optimistic user input after server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 0, 1)).toEqual([]);
});

test("mergeMessages shows server human instead of optimistic duplicate after first response", () => {
  const serverHuman = {
    id: "server-human-1",
    type: "human",
    content: "hello",
  } as Message;
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;
  const visibleOptimistic = getVisibleOptimisticMessages(
    [optimisticHuman],
    0,
    1,
  );

  expect(mergeMessages([], [serverHuman], visibleOptimistic)).toEqual([
    serverHuman,
  ]);
});

test("mergeHistoryLiveMessages filters summary and hidden live messages", () => {
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "hello",
  } as Message;
  const summary = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "summary",
  } as Message;
  const hidden = {
    id: "hidden-1",
    type: "human",
    content: "hidden",
    additional_kwargs: { hide_from_ui: true },
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "answer",
  } as Message;

  expect(
    mergeHistoryLiveMessages([historyHuman], [summary, hidden, liveAi], []),
  ).toEqual([historyHuman]);
});

test("mergeHistoryLiveMessages treats history as the stable transcript", () => {
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "history",
  } as Message;
  const liveHuman = {
    id: "human-1",
    type: "human",
    content: "live",
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "answer",
  } as Message;

  expect(
    mergeHistoryLiveMessages([historyHuman], [liveHuman, liveAi], []),
  ).toEqual([historyHuman, liveAi]);
});

test("mergeHistoryLiveMessages only appends live suffix after the latest history overlap", () => {
  const oldHuman = {
    id: "human-old",
    type: "human",
    content: "1",
  } as Message;
  const missingOldTool = {
    id: "clarification:call-1",
    type: "tool",
    tool_call_id: "call-1",
    content: "old clarification result missing from run_events",
  } as Message;
  const latestHuman = {
    id: "human-latest",
    type: "human",
    content: "今天深圳天气怎么样？",
  } as Message;
  const latestErrorFallback = {
    id: "error-fallback",
    type: "ai",
    content:
      "The configured LLM provider is temporarily unavailable after multiple retries.",
    additional_kwargs: { deerflow_error_fallback: true },
  } as Message;

  expect(
    mergeHistoryLiveMessages(
      [oldHuman, latestHuman],
      [oldHuman, missingOldTool, latestHuman, latestErrorFallback],
      [],
    ),
  ).toEqual([oldHuman, latestHuman, latestErrorFallback]);
});

test("getVisibleOptimisticMessages keeps optimistic user input until server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 0, 0)).toEqual([
    optimisticHuman,
  ]);
});

test("getVisibleOptimisticMessages keeps non-human optimistic status messages", () => {
  const optimisticAi = {
    id: "opt-ai-1",
    type: "ai",
    content: "Uploading files...",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticAi], 0, 1)).toEqual([
    optimisticAi,
  ]);
});

test("getVisibleOptimisticMessages hides the upload optimistic pair after server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "upload this",
  } as Message;
  const optimisticUploadingAi = {
    id: "opt-ai-uploading",
    type: "ai",
    content: "Uploading files...",
  } as Message;

  expect(
    getVisibleOptimisticMessages(
      [optimisticHuman, optimisticUploadingAi],
      0,
      1,
    ),
  ).toEqual([]);
});

test("getVisibleOptimisticMessages hides optimistic user input after later server turns", () => {
  const optimisticHuman = {
    id: "opt-human-2",
    type: "human",
    content: "follow up",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 3, 4)).toEqual([]);
  expect(getVisibleOptimisticMessages([optimisticHuman], 3, 3)).toEqual([
    optimisticHuman,
  ]);
});

test("runMessagesPageHasMore reads backend snake_case pagination field", () => {
  expect(runMessagesPageHasMore({ data: [], has_more: true })).toBe(true);
  expect(runMessagesPageHasMore({ data: [], has_more: false })).toBe(false);
});

test("runMessagesPageHasMore keeps compatibility with camelCase pagination field", () => {
  expect(runMessagesPageHasMore({ data: [], hasMore: true })).toBe(true);
});

test("getOldestRunMessageSeq returns the cursor for the next older run page", () => {
  expect(
    getOldestRunMessageSeq([runMessage(8), runMessage(9), runMessage(10)]),
  ).toBe(8);
});

test("getOldestRunMessageSeq ignores rows without seq", () => {
  expect(getOldestRunMessageSeq([runMessage()])).toBeNull();
});

test("getNextRunMessagesBeforeSeq keeps runs pending when has_more lacks seq", () => {
  expect(
    getNextRunMessagesBeforeSeq({ data: [runMessage()], has_more: true }),
  ).toBeUndefined();
});

test("getNextRunMessagesBeforeSeq marks runs loaded when no more pages exist", () => {
  expect(
    getNextRunMessagesBeforeSeq({ data: [runMessage()], has_more: false }),
  ).toBeNull();
});

test("buildRunMessagesUrl encodes path segments and optional before_seq", () => {
  expect(
    buildRunMessagesUrl(
      "https://api.example.test/",
      "thread/with space",
      "run?one",
      18,
    ),
  ).toBe(
    "https://api.example.test/api/threads/thread%2Fwith%20space/runs/run%3Fone/messages?before_seq=18",
  );
});

test("buildRunMessagesUrl omits before_seq when loading the latest page", () => {
  expect(
    buildRunMessagesUrl("https://api.example.test", "thread-1", "run-1"),
  ).toBe("https://api.example.test/api/threads/thread-1/runs/run-1/messages");
});

test("buildRunMessagesUrl returns a relative URL when using the nginx proxy", () => {
  expect(buildRunMessagesUrl("", "thread-1", "run-1", 42)).toBe(
    "/api/threads/thread-1/runs/run-1/messages?before_seq=42",
  );
});

test("buildThreadMessagesUrl encodes thread id and includes limit", () => {
  expect(
    buildThreadMessagesUrl("https://api.example.test/", "thread/with space"),
  ).toBe(
    "https://api.example.test/api/threads/thread%2Fwith%20space/messages?limit=50",
  );
});

test("buildThreadMessagesUrl includes before_seq when loading older history", () => {
  expect(buildThreadMessagesUrl("", "thread-1", 42, 100)).toBe(
    "/api/threads/thread-1/messages?limit=100&before_seq=42",
  );
});

test("mergeRunMessageRows sorts and deduplicates by thread seq", () => {
  const oldRow = runMessage(2);
  const replacedRow = {
    ...runMessage(2),
    content: { id: "ai-2", type: "ai", content: "new" } as Message,
  };
  const newerRow = runMessage(3);
  const olderRow = runMessage(1);

  expect(
    mergeRunMessageRows([oldRow, newerRow], [olderRow, replacedRow]),
  ).toEqual([olderRow, replacedRow, newerRow]);
});

test("runEventRowsToMessages filters middleware, hidden, and summary rows", () => {
  const visible = {
    ...runMessage(1),
    content: { id: "human-1", type: "human", content: "visible" } as Message,
  };
  const middleware = {
    ...runMessage(2),
    metadata: { caller: "middleware:summary" },
    content: { id: "ai-1", type: "ai", content: "internal" } as Message,
  };
  const hidden = {
    ...runMessage(3),
    content: {
      id: "hidden-1",
      type: "human",
      content: "hidden",
      additional_kwargs: { hide_from_ui: true },
    } as Message,
  };
  const summary = {
    ...runMessage(4),
    content: {
      id: "summary-1",
      type: "human",
      name: "summary",
      content: "summary",
    } as Message,
  };

  expect(
    runEventRowsToMessages([visible, middleware, hidden, summary]),
  ).toEqual([visible.content]);
});
