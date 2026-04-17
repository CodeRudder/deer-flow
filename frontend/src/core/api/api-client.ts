"use client";

import { Client as LangGraphClient } from "@langchain/langgraph-sdk/client";

import { getBackendBaseURL, getLangGraphBaseURL } from "../config";

import { sanitizeRunStreamOptions } from "./stream-mode";

function createCompatibleClient(isMock?: boolean): LangGraphClient {
  const client = new LangGraphClient({
    apiUrl: getLangGraphBaseURL(isMock),
  });

  const originalRunStream = client.runs.stream.bind(client.runs);
  client.runs.stream = ((threadId, assistantId, payload) =>
    originalRunStream(
      threadId,
      assistantId,
      sanitizeRunStreamOptions(payload),
    )) as typeof client.runs.stream;

  const originalJoinStream = client.runs.joinStream.bind(client.runs);
  client.runs.joinStream = ((threadId, runId, options) =>
    originalJoinStream(
      threadId,
      runId,
      sanitizeRunStreamOptions(options),
    )) as typeof client.runs.joinStream;

  // Override getHistory to route through Gateway, which trims large message lists
  // to first 10 + last 40 messages with a placeholder for omitted middle messages.
  type GetHistoryOptions = Parameters<typeof client.threads.getHistory>[1];
  const originalGetHistory = client.threads.getHistory.bind(client.threads);
  client.threads.getHistory = (async (threadId: string, options?: GetHistoryOptions) => {
    // Extract checkpoint_id from before config if it's a Config object
    let before: string | undefined;
    if (options?.before) {
      const b = options.before as Record<string, unknown>;
      if (typeof b === "string") {
        before = b;
      } else if (b?.configurable && typeof (b.configurable as Record<string, unknown>)?.checkpoint_id === "string") {
        before = (b.configurable as Record<string, unknown>).checkpoint_id as string;
      }
    }

    try {
      const res = await fetch(`${getBackendBaseURL()}/api/threads/${threadId}/history`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: options?.limit ?? 10, before }),
        signal: options?.signal,
      });
      if (!res.ok) throw new Error("Gateway history fetch failed");
      return res.json();
    } catch {
      // Fallback to LangGraph server if Gateway fails
      return originalGetHistory(threadId, options);
    }
  }) as typeof client.threads.getHistory;

  // Override threads.search to route through Gateway, which strips heavy
  // fields (messages bulk, viewed_images, artifacts) from each thread's values.
  const originalSearch = client.threads.search.bind(client.threads);
  client.threads.search = (async (params?: Record<string, unknown>) => {
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/threads/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params ?? {}),
      });
      if (!res.ok) throw new Error("Gateway search failed");
      return res.json();
    } catch {
      // Fallback to LangGraph server if Gateway fails
      return originalSearch(params);
    }
  }) as typeof client.threads.search;

  return client;
}

const _clients = new Map<string, LangGraphClient>();
export function getAPIClient(isMock?: boolean): LangGraphClient {
  const cacheKey = isMock ? "mock" : "default";
  let client = _clients.get(cacheKey);

  if (!client) {
    client = createCompatibleClient(isMock);
    _clients.set(cacheKey, client);
  }

  return client;
}
