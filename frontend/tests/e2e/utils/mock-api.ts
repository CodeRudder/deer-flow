/**
 * Shared mock helpers for E2E tests.
 *
 * Intercepts all LangGraph / Backend API endpoints so tests can run without
 * a real backend.  Each test file imports `mockLangGraphAPI` and
 * `handleRunStream` from here.
 */

import type { Page, Route } from "@playwright/test";

// ---------------------------------------------------------------------------
// Constants — deterministic IDs used across tests
// ---------------------------------------------------------------------------

export const MOCK_THREAD_ID = "00000000-0000-0000-0000-000000000001";
export const MOCK_THREAD_ID_2 = "00000000-0000-0000-0000-000000000002";
export const MOCK_RUN_ID = "00000000-0000-0000-0000-000000000099";

/**
 * The assistant message a joined run (`MockThread.activeRun`) appends, exported
 * so a spec can assert on the text without restating it.
 */
export const JOINED_RUN_MESSAGE_ID = "msg-ai-joined-run";
export const JOINED_RUN_REPLY = "Joined the run that was already in flight.";

const MOCK_AUTH_USER = {
  id: "default",
  email: "default@test.local",
  system_role: "admin",
  needs_setup: false,
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type MockThread = {
  thread_id: string;
  title?: string;
  updated_at?: string;
  agent_name?: string;
  metadata?: Record<string, unknown>;
  messages?: unknown[];
  artifacts?: string[];
  /**
   * Per-thread run state, exactly as the gateway's `ThreadResponse` carries it
   * (`running` while a run is in flight, `idle` once it settles). Defaults to
   * `idle` so existing fixtures keep describing settled threads.
   */
  status?: string;
  /**
   * Opt-in: declare that this thread has a run in flight, exactly as the
   * gateway's runs list would report it. The chat page adopts such a run on
   * open (`pickActiveRunId` + `joinStream`), so the composer starts in its stop
   * state and the reply streams in without the user sending anything.
   *
   * Fixtures that omit it keep the settled `success` run the runs-list mock has
   * always returned — declaring nothing must never change a spec's behavior.
   */
  activeRun?: { run_id: string; status: "pending" | "running" };
};

export type MockAgent = {
  name: string;
  description?: string;
  system_prompt?: string;
};

export type MockSkill = {
  name: string;
  description: string;
  category?: string;
  license?: string | null;
  enabled?: boolean;
};

export type MockAPIOptions = {
  threads?: MockThread[];
  agents?: MockAgent[];
  skills?: MockSkill[];
};

const DEFAULT_SKILLS: MockSkill[] = [
  {
    name: "data-analysis",
    description: "Analyze structured data and produce charts.",
    category: "public",
    enabled: true,
  },
  {
    name: "frontend-design",
    description: "Create polished frontend interfaces.",
    category: "public",
    enabled: true,
  },
  {
    name: "disabled-skill",
    description: "Hidden from slash autocomplete.",
    category: "public",
    enabled: false,
  },
];

function mockStreamMessages() {
  return [
    {
      type: "human",
      id: "msg-human-1",
      content: [{ type: "text", text: "Hello" }],
    },
    {
      type: "ai",
      id: "msg-ai-1",
      content: "Hello from DeerFlow!",
    },
  ];
}

// ---------------------------------------------------------------------------
// mockLangGraphAPI
// ---------------------------------------------------------------------------

/**
 * Mock all LangGraph API endpoints that the frontend calls on page load and
 * during message sending.  Without these mocks the pages would hang waiting
 * for a real backend.
 */
export function mockLangGraphAPI(page: Page, options?: MockAPIOptions) {
  let threads = [...(options?.threads ?? [])];
  const agents = options?.agents ?? [];
  const skills = options?.skills ?? DEFAULT_SKILLS;

  const upsertThread = (thread: MockThread) => {
    threads = [
      thread,
      ...threads.filter((existing) => existing.thread_id !== thread.thread_id),
    ];
  };

  const threadSearchResult = (thread: MockThread) => ({
    thread_id: thread.thread_id,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: thread.updated_at ?? "2025-01-01T00:00:00Z",
    metadata: {
      ...(thread.metadata ?? {}),
      ...(thread.agent_name ? { agent_name: thread.agent_name } : {}),
    },
    status: thread.status ?? "idle",
    values: { title: thread.title ?? "Untitled" },
  });

  /**
   * The runs list for a thread, newest first — which for these fixtures is a
   * single run: the declared in-flight one when there is one, else a settled
   * `success` run.
   */
  const runEntriesFor = (thread: MockThread) => [
    {
      run_id: thread.activeRun?.run_id ?? `run-${thread.thread_id}`,
      thread_id: thread.thread_id,
      assistant_id: "lead_agent",
      status: thread.activeRun?.status ?? "success",
      metadata: {},
      kwargs: {},
      created_at: "2025-01-01T00:00:00Z",
      updated_at: thread.updated_at ?? "2025-01-01T00:00:00Z",
    },
  ];

  // Auth — keep workspace tests independent from a real gateway session.
  void page.route("**/api/v1/auth/me", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_AUTH_USER),
      });
    }
    return route.fallback();
  });

  void page.route("**/api/v1/auth/setup-status", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ needs_setup: false }),
      });
    }
    return route.fallback();
  });

  void page.route("**/api/v1/auth/logout", (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({ status: 204 });
    }
    return route.fallback();
  });

  void page.route("**/api/channels/providers", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ enabled: false, providers: [] }),
      });
    }
    return route.fallback();
  });

  void page.route("**/api/channels/connections", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ connections: [] }),
      });
    }
    return route.fallback();
  });

  // Thread search — sidebar thread list & chats list page
  void page.route("**/api/langgraph/threads/search", async (route) => {
    const body = threads.map(threadSearchResult);

    let limit: number | undefined;
    let offset = 0;
    try {
      const postData = route.request().postDataJSON() as {
        limit?: number;
        offset?: number;
      } | null;
      if (postData) {
        if (typeof postData.limit === "number") {
          limit = postData.limit;
        }
        if (typeof postData.offset === "number") {
          offset = postData.offset;
        }
      }
    } catch {
      // No / invalid JSON body — fall back to returning the full list.
    }

    const sliced =
      typeof limit === "number" ? body.slice(offset, offset + limit) : body;

    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(sliced),
    });
  });

  // Thread create — called when user sends first message in a new chat
  void page.route("**/api/langgraph/threads", (route) => {
    if (route.request().method() === "POST") {
      upsertThread({
        thread_id: MOCK_THREAD_ID,
        title: "New Chat",
        updated_at: new Date().toISOString(),
        messages: mockStreamMessages(),
      });
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          thread_id: MOCK_THREAD_ID,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          metadata: {},
          status: "idle",
          values: {},
        }),
      });
    }
    return route.fallback();
  });

  // Thread update (PATCH) — metadata update after creation
  void page.route("**/api/langgraph/threads/*", (route) => {
    const threadId = decodeURIComponent(
      new URL(route.request().url()).pathname.split("/").at(-1) ?? "",
    );
    const matchingThread = threads.find(
      (thread) => thread.thread_id === threadId,
    );
    if (route.request().method() === "GET") {
      if (!matchingThread) {
        return route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Thread not found" }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(threadSearchResult(matchingThread)),
      });
    }
    if (route.request().method() === "PATCH") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ thread_id: MOCK_THREAD_ID }),
      });
    }
    if (route.request().method() === "DELETE") {
      threads = threads.filter((thread) => thread.thread_id !== threadId);
      return route.fulfill({
        status: 204,
      });
    }
    return route.fallback();
  });

  void page.route(/\/api\/threads\/[^/]+$/, (route) => {
    if (route.request().method() === "DELETE") {
      return route.fulfill({
        status: 204,
      });
    }
    return route.fallback();
  });

  // Thread history — useStream fetches state history on mount
  void page.route("**/api/langgraph/threads/*/history", (route) => {
    const url = route.request().url();

    // For threads that exist in our mock data, return history with messages
    const matchingThread = threads.find((t) => url.includes(t.thread_id));
    if (matchingThread) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            values: {
              title: matchingThread.title ?? "Untitled",
              messages: matchingThread.messages ?? [
                {
                  type: "human",
                  id: `msg-human-${matchingThread.thread_id}`,
                  content: [{ type: "text", text: "Previous question" }],
                },
                {
                  type: "ai",
                  id: `msg-ai-${matchingThread.thread_id}`,
                  content: `Response in thread ${matchingThread.title ?? matchingThread.thread_id}`,
                },
              ],
              artifacts: matchingThread.artifacts ?? [],
            },
            next: [],
            metadata: {},
            created_at: "2025-01-01T00:00:00Z",
            parent_config: null,
          },
        ]),
      });
    }

    // New threads — empty history
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });

  // Thread state — getState for individual thread
  void page.route("**/api/langgraph/threads/*/state", (route) => {
    if (route.request().method() === "GET") {
      const url = route.request().url();
      const matchingThread = threads.find((t) => url.includes(t.thread_id));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          values: {
            title: matchingThread?.title ?? "Untitled",
            messages: matchingThread
              ? (matchingThread.messages ?? [
                  {
                    type: "human",
                    id: `msg-human-${matchingThread.thread_id}`,
                    content: [{ type: "text", text: "Previous question" }],
                  },
                  {
                    type: "ai",
                    id: `msg-ai-${matchingThread.thread_id}`,
                    content: `Response in thread ${matchingThread.title ?? matchingThread.thread_id}`,
                  },
                ])
              : [],
            artifacts: matchingThread?.artifacts ?? [],
          },
          next: [],
          metadata: {},
          created_at: "2025-01-01T00:00:00Z",
        }),
      });
    }
    return route.fallback();
  });

  // The URL carries a query string (e.g. `?limit=10&offset=0`), which Playwright
  // glob `*` does NOT cross, so we match with a regex anchored to `/runs`
  // followed by `?` or end-of-string.  This must NOT match `/runs/stream`.
  void page.route(/\/api\/langgraph\/threads\/[^/]+\/runs(\?|$)/, (route) => {
    if (route.request().method() === "GET") {
      const url = route.request().url();
      const matchingThread = threads.find((t) => url.includes(t.thread_id));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          matchingThread ? runEntriesFor(matchingThread) : [],
        ),
      });
    }
    return route.fallback();
  });

  // Run detail — `GET /threads/{threadId}/runs/{runId}`. The client wraps
  // `joinStream` to look the run up first and skip runs that already finished
  // (`shouldSkipReconnect` in `src/core/api/api-client.ts`), so the adoption
  // path reads this endpoint before it opens the stream. Answering it keeps the
  // adoption hermetic; unmocked it would reach the gateway.
  //
  // `(?!stream)` keeps the submit endpoint (`.../runs/stream`) on its own
  // handler below rather than being read here as a run called "stream".
  void page.route(
    /\/api\/langgraph\/threads\/[^/]+\/runs\/(?!stream(\?|$))[^/]+(\?|$)/,
    (route) => {
      if (route.request().method() !== "GET") {
        return route.fallback();
      }
      const url = route.request().url();
      const matchingThread = threads.find((t) => url.includes(t.thread_id));
      if (!matchingThread) {
        return route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Run not found" }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(runEntriesFor(matchingThread)[0]),
      });
    },
  );

  void page.route(
    /\/api\/threads\/([^/]+)\/runs\/([^/]+)\/messages/,
    (route) => {
      if (route.request().method() === "GET") {
        const url = route.request().url();
        const matchingThread = threads.find((t) =>
          url.includes(`/api/threads/${t.thread_id}/runs/`),
        );
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: (matchingThread?.messages ?? []).map((message, index) => ({
              run_id: `run-${matchingThread?.thread_id ?? "unknown"}`,
              content: message,
              metadata: { caller: "lead_agent" },
              created_at: `2025-01-01T00:00:${String(index).padStart(2, "0")}Z`,
            })),
            hasMore: false,
          }),
        });
      }
      return route.fallback();
    },
  );

  // Run stream — returns a minimal SSE response with an AI message
  const handleMockRunStream = (route: Route) => {
    upsertThread({
      thread_id: MOCK_THREAD_ID,
      title: "New Chat",
      updated_at: new Date().toISOString(),
      messages: mockStreamMessages(),
    });
    return handleRunStream(route);
  };

  void page.route("**/api/langgraph/runs/stream", handleMockRunStream);
  void page.route(
    "**/api/langgraph/threads/*/runs/stream",
    handleMockRunStream,
  );

  // Join stream — `GET /threads/{threadId}/runs/{runId}/stream`: the endpoint
  // the SDK's `joinStream` opens to adopt a run this page did not start (see
  // `MockThread.activeRun`). The two globs above cannot match it — their single
  // `*` stands for the thread id, so the extra `/{runId}` segment fails to
  // match — hence a pattern of its own.
  void page.route(
    /\/api\/langgraph\/threads\/[^/]+\/runs\/[^/]+\/stream(\?|$)/,
    (route) => {
      if (route.request().method() !== "GET") {
        return route.fallback();
      }
      const url = route.request().url();
      const matchingThread = threads.find((t) => url.includes(t.thread_id));
      return handleJoinedRunStream(route, matchingThread);
    },
  );

  // Models list — model picker dropdown
  void page.route("**/api/models", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          models: [],
          token_usage: { enabled: false },
        }),
      });
    }
    return route.fallback();
  });

  // Skills list — settings page and slash autocomplete
  void page.route("**/api/skills", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ skills }),
      });
    }
    return route.fallback();
  });

  // Follow-up suggestions — input box auto-suggest after AI response
  void page.route("**/api/threads/*/suggestions", (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ suggestions: [] }),
      });
    }
    return route.fallback();
  });

  // Agents list — sidebar & gallery page
  void page.route("**/api/agents", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ agents }),
      });
    }
    return route.fallback();
  });

  // Individual agent — agent chat page
  void page.route("**/api/agents/*", (route) => {
    if (route.request().method() === "GET") {
      const url = route.request().url();
      const agent = agents.find((a) => url.endsWith(`/api/agents/${a.name}`));
      if (agent) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(agent),
        });
      }
    }
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Agent not found" }),
    });
  });
}

// ---------------------------------------------------------------------------
// handleRunStream
// ---------------------------------------------------------------------------

/**
 * Build a minimal SSE stream that the LangGraph SDK can parse.
 * The stream returns a single AI message: "Hello from DeerFlow!".
 */
export function handleRunStream(route: Route) {
  const events = [
    {
      event: "metadata",
      data: { run_id: MOCK_RUN_ID, thread_id: MOCK_THREAD_ID },
    },
    {
      event: "values",
      data: {
        messages: mockStreamMessages(),
      },
    },
    { event: "end", data: {} },
  ];

  const body = events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");

  return route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body,
  });
}

// ---------------------------------------------------------------------------
// handleJoinedRunStream
// ---------------------------------------------------------------------------

/**
 * The SSE stream a joined run replays, in the same shape `handleRunStream`
 * uses (`metadata` → `values` → `end`).
 *
 * A `values` event carries the whole thread state, so this reports what the
 * thread already had plus one new assistant message — the real backend's
 * "here is the state as of now, and here is what the run went on to produce".
 * The caller's declared fixture messages are reused, which keeps the transcript
 * intact for threads that already have turns.
 *
 * The run id is read back out of the request URL rather than from a constant,
 * so the stream names the run the page actually asked to join.
 */
export function handleJoinedRunStream(route: Route, thread?: MockThread) {
  const requestUrl = new URL(route.request().url());
  const runId = requestUrl.pathname.split("/").at(-2) ?? MOCK_RUN_ID;
  const threadId = thread?.thread_id ?? MOCK_THREAD_ID;

  const events = [
    {
      event: "metadata",
      data: { run_id: runId, thread_id: threadId },
    },
    {
      event: "values",
      data: {
        title: thread?.title ?? "Untitled",
        messages: [
          ...(thread?.messages ?? mockStreamMessages()),
          {
            type: "ai",
            id: JOINED_RUN_MESSAGE_ID,
            content: JOINED_RUN_REPLY,
          },
        ],
        artifacts: thread?.artifacts ?? [],
      },
    },
    { event: "end", data: {} },
  ];

  const body = events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");

  return route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body,
  });
}
