import { createServer, type Server, type ServerResponse } from "node:http";
import type { AddressInfo, Socket } from "node:net";

import { devices, expect, test, type Page } from "@playwright/test";

import {
  MOCK_RUN_ID,
  MOCK_THREAD_ID,
  mockLangGraphAPI,
  type MockThread,
} from "./utils/mock-api";

/**
 * Mobile streaming and scroll affordances (T10, TEST_FLOW F4-4/5/8, gap G8).
 *
 * Every other mobile spec answers `/runs/stream` with `route.fulfill()`, which
 * delivers the whole reply in one `values` event. That proves "a reply renders"
 * and nothing about the *in-flight* states: the stop button, the incremental
 * growth of the text, the "back to bottom" affordance.
 *
 * Holding a `page.route()` handler open does not work either — the SDK's
 * AbortController fires and `net::ERR_ABORTED` is observable, but the rejection
 * never reaches the SDK, so the composer stays stuck on "Stop generating"
 * forever (measured; TEST_FLOW「流式测试的技术前提（G8）」).
 *
 * So this spec starts a real `node:http` server that speaks `text/event-stream`
 * and hands the test the write handle: each chunk is pushed only when the test
 * asks for it, which is what turns "the text grew between chunk N and chunk
 * N+1" into a deterministic assertion instead of a race.
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const CHAT_PATH = `/workspace/chats/${MOCK_THREAD_ID}`;
const NEW_CHAT_PATH = "/workspace/chats/new";

const CHUNK_ONE = "Streaming chunk one.";
const CHUNK_TWO = `${CHUNK_ONE} Chunk two.`;
const CHUNK_THREE = `${CHUNK_TWO} Chunk three.`;

/** A model list, so the composer pins its mode instead of flushing one late. */
const MODEL = {
  id: "mock-model",
  name: "mock-model",
  model: "mock-model",
  display_name: "Mock Model",
  supports_thinking: true,
  supports_reasoning_effort: true,
};

function mockModels(page: Page) {
  return page.route("**/api/models", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        models: [MODEL],
        vision_models: [],
        token_usage: { enabled: false },
      }),
    }),
  );
}

const HUMAN_MESSAGE = {
  type: "human",
  id: "msg-human-1",
  content: [{ type: "text", text: "Please stream the reply" }],
};

function aiMessage(text: string) {
  return { type: "ai", id: "msg-ai-stream", content: text };
}

/** `count` human/assistant pairs — enough to overflow the transcript. */
function longTranscript(count: number) {
  return Array.from({ length: count }, (_, index) => [
    {
      type: "human",
      id: `msg-human-${index}`,
      content: [{ type: "text", text: `Question number ${index}` }],
    },
    { type: "ai", id: `msg-ai-${index}`, content: `Answer number ${index}` },
  ]).flat();
}

// ---------------------------------------------------------------------------
// Loopback SSE server
// ---------------------------------------------------------------------------

type LoopbackSse = {
  /** Base URL to redirect `/runs/stream` to. */
  origin: string;
  /** How many SSE connections the app has opened. */
  connections: () => number;
  /** True once the client hung up — i.e. the run was aborted. */
  disconnected: () => boolean;
  /** Resolves once a client is connected and the headers are flushed. */
  waitForConnection: () => Promise<void>;
  /** Resolves when the client disconnects. */
  waitForDisconnect: () => Promise<void>;
  /** Push one SSE event. */
  write: (event: string, data: unknown) => void;
  /** Close the body cleanly; the SDK sees the run complete. */
  end: () => void;
  /** Shut the listener down. Safe to call more than once. */
  close: () => Promise<void>;
};

/**
 * A single-request SSE server whose body the test writes by hand.
 *
 * `Content-Location` is the header the SDK reads to learn the created run's
 * thread/run ids (`client.js` `REGEX_RUN_METADATA`); without it `onCreated`
 * never fires and the composer's stop path has no run to cancel.
 */
async function startLoopbackSse(): Promise<LoopbackSse> {
  const sockets = new Set<Socket>();
  let response: ServerResponse | null = null;
  let connections = 0;
  let didDisconnect = false;
  let connected: Array<() => void> = [];
  let disconnected: Array<() => void> = [];

  const server: Server = createServer((request, res) => {
    const headers = {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Expose-Headers": "Content-Location",
      "Content-Location": `/threads/${MOCK_THREAD_ID}/runs/${MOCK_RUN_ID}`,
    };

    if (request.method === "OPTIONS") {
      res.writeHead(204, headers);
      res.end();
      return;
    }

    // Drain the request body so the POST completes from the client's side.
    request.resume();
    connections += 1;
    response = res;
    res.writeHead(200, headers);
    res.flushHeaders();

    const pendingConnect = connected;
    connected = [];
    for (const resolve of pendingConnect) {
      resolve();
    }

    // The *response* stream closing is the client hanging up. (The request
    // stream's own `close` fires when its body finishes uploading, which for a
    // normal POST happens immediately — it says nothing about an abort.)
    res.on("close", () => {
      didDisconnect = true;
      const pendingDisconnect = disconnected;
      disconnected = [];
      for (const resolve of pendingDisconnect) {
        resolve();
      }
    });
  });

  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });

  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  const { port } = server.address() as AddressInfo;

  const settle = (
    already: () => boolean,
    queue: Array<() => void>,
    label: string,
  ) =>
    new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error(`loopback SSE: no ${label} within 10s`)),
        10_000,
      );
      const done = () => {
        clearTimeout(timer);
        resolve();
      };
      if (already()) {
        done();
        return;
      }
      queue.push(done);
    });

  return {
    origin: `http://127.0.0.1:${port}`,
    connections: () => connections,
    disconnected: () => didDisconnect,
    waitForConnection: () =>
      settle(() => connections > 0 && response !== null, connected, "connection"),
    waitForDisconnect: () =>
      settle(() => didDisconnect, disconnected, "disconnect"),
    write: (event, data) => {
      if (!response || response.writableEnded) {
        throw new Error(`SSE event "${event}" written after the stream closed`);
      }
      response.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    },
    end: () => response?.end(),
    close: async () => {
      for (const socket of sockets) {
        socket.destroy();
      }
      sockets.clear();
      await new Promise<void>((resolve) => {
        server.close(() => resolve());
      });
    },
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Points the app's `/runs/stream` calls at the loopback server. */
async function routeStreamTo(page: Page, origin: string) {
  await page.route("**/runs/stream", (route) =>
    route.continue({ url: `${origin}/api/langgraph/runs/stream` }),
  );
  // The SDK fires this (unawaited) when a run is stopped.
  await page.route(/\/runs\/[^/]+\/cancel/, (route) =>
    route.fulfill({ status: 204 }),
  );
}

function textarea(page: Page) {
  return page.getByTestId("mobile-composer").locator("textarea");
}

function sendButton(page: Page) {
  return page.getByTestId("mobile-composer-send");
}

async function openComposer(page: Page) {
  await expect(page.getByTestId("mobile-composer")).toBeVisible({
    timeout: 15_000,
  });
}

async function send(page: Page, text: string) {
  await textarea(page).fill(text);
  await sendButton(page).click();
}

/**
 * The transcript scroller. `MessageList` mounts `Conversation` (the
 * stick-to-bottom root carrying `role="log"`) and the library puts the
 * `overflow: auto` on that root's first child — the same node the page's own
 * `findTranscriptScroller()` picks (see `use-scroll-to-bottom.ts`).
 */
function scroller(page: Page) {
  return page.locator('[role="log"] > *').first();
}

function scrollBottomButton(page: Page) {
  return page.getByTestId("mobile-chat-scroll-bottom");
}

/**
 * The reply as rendered, from the first chunk marker to the end of the
 * transcript. Sampling its length between chunks is the assertion a
 * single-shot mock cannot make: the text must grow *while the response body is
 * still open*.
 */
async function replyText(page: Page) {
  const transcript = await page.locator('[role="log"]').innerText();
  const index = transcript.indexOf(CHUNK_ONE);
  return index === -1 ? "" : transcript.slice(index);
}

let loopback: LoopbackSse | null = null;

test.afterEach(async () => {
  await loopback?.close();
  loopback = null;
});

// ---------------------------------------------------------------------------
// F4-4 · in-flight stream
// ---------------------------------------------------------------------------

test.describe("Mobile chat streaming", () => {
  test("the reply grows chunk by chunk while the stop affordance stays live", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    loopback = await startLoopbackSse();
    await routeStreamTo(page, loopback.origin);

    await page.goto(NEW_CHAT_PATH);
    await openComposer(page);
    await send(page, "Please stream the reply");

    const stream = loopback;
    await stream.waitForConnection();
    const messages = [HUMAN_MESSAGE, aiMessage("")];
    const push = (text: string) => {
      messages[messages.length - 1] = aiMessage(text);
      stream.write("values", {
        title: "New Chat",
        messages,
        artifacts: [],
      });
    };

    // Chunk 1 — the run is open, so the composer must be offering stop.
    push(CHUNK_ONE);
    const stop = sendButton(page);
    await expect(stop).toHaveAttribute("aria-label", "Stop generating", {
      timeout: 10_000,
    });
    await expect(stop).toBeEnabled();
    await expect(page.getByText(CHUNK_ONE)).toBeVisible();

    const afterFirst = await replyText(page);
    expect(afterFirst).toContain(CHUNK_ONE);
    // Chunk 2 is not on screen yet — it has not been written yet.
    expect(afterFirst).not.toContain("Chunk two.");

    // Chunk 2 — the text grows, and it does so before the stream ends.
    push(CHUNK_TWO);
    await expect(page.getByText("Chunk two.")).toBeVisible({ timeout: 10_000 });
    const afterSecond = await replyText(page);
    expect(afterSecond.length).toBeGreaterThan(afterFirst.length);
    expect(afterSecond).not.toContain("Chunk three.");

    // Chunk 3 — same again, and stop is still live.
    push(CHUNK_THREE);
    await expect(page.getByText("Chunk three.")).toBeVisible({
      timeout: 10_000,
    });
    const afterThird = await replyText(page);
    expect(afterThird.length).toBeGreaterThan(afterSecond.length);
    await expect(stop).toHaveAttribute("aria-label", "Stop generating");

    // The proof that all of the above happened *in flight*: the response body
    // is still open, one connection, nothing aborted.
    expect(stream.connections()).toBe(1);
    expect(stream.disconnected()).toBe(false);

    // ...and finishing the run hands the composer back.
    stream.end();
    await expect(stop).toHaveAttribute("aria-label", "Send", {
      timeout: 10_000,
    });
  });

  // -------------------------------------------------------------------------
  // F4-5 · stop
  // -------------------------------------------------------------------------

  test("stopping mid-stream aborts the run and returns the composer to idle", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    loopback = await startLoopbackSse();
    await routeStreamTo(page, loopback.origin);

    await page.goto(NEW_CHAT_PATH);
    await openComposer(page);
    await send(page, "Please stream the reply");

    const stream = loopback;
    await stream.waitForConnection();
    stream.write("values", {
      title: "New Chat",
      messages: [HUMAN_MESSAGE, aiMessage("Half a reply.")],
      artifacts: [],
    });

    const stop = sendButton(page);
    await expect(stop).toHaveAttribute("aria-label", "Stop generating", {
      timeout: 10_000,
    });
    await expect(page.getByText("Half a reply.")).toBeVisible();
    expect(stream.disconnected()).toBe(false);

    await stop.click();

    // The run is really gone: the loopback saw its client hang up.
    await stream.waitForDisconnect();
    // ...and the composer is usable again rather than stuck on 停止生成.
    await expect(stop).toHaveAttribute("aria-label", "Send", {
      timeout: 10_000,
    });
    await expect(textarea(page)).toBeEnabled();
    await expect(page.getByTestId("mobile-composer-plus")).toBeEnabled();
  });

  // -------------------------------------------------------------------------
  // F4-8 · back to bottom
  // -------------------------------------------------------------------------

  test("scrolling away from the bottom reveals the back-to-bottom affordance", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    loopback = await startLoopbackSse();
    await routeStreamTo(page, loopback.origin);

    // The flow is a send, not a cold load: this page starts on `/chats/new`,
    // so the thread id settles with the run and `useScrollToBottom()`
    // re-resolves the transcript then. The case below covers the other way in,
    // where the id never changes after the page paints.
    await page.goto(NEW_CHAT_PATH);
    await openComposer(page);
    await send(page, "Please stream the reply");

    const stream = loopback;
    await stream.waitForConnection();
    stream.write("values", {
      title: "New Chat",
      messages: [HUMAN_MESSAGE, ...longTranscript(24)],
      artifacts: [],
    });
    await expect(page.getByText("Answer number 23")).toBeVisible({
      timeout: 15_000,
    });

    const transcript = scroller(page);
    const distanceToBottom = () =>
      transcript.evaluate((element) =>
        Math.round(
          element.scrollHeight - element.scrollTop - element.clientHeight,
        ),
      );
    // Drive on observed geometry, not on a timer: wait for the transcript's
    // own scroll-to-bottom animation to settle before taking over.
    await expect.poll(distanceToBottom, { timeout: 15_000 }).toBeLessThanOrEqual(
      16,
    );
    await expect(scrollBottomButton(page)).toHaveCount(0);

    await transcript.evaluate((element) => {
      element.scrollTop = 0;
    });

    const affordance = scrollBottomButton(page);
    await expect(affordance).toBeVisible({ timeout: 10_000 });
    await affordance.click();
    await expect(affordance).toHaveCount(0, { timeout: 10_000 });
    await expect.poll(distanceToBottom, { timeout: 10_000 }).toBeLessThanOrEqual(
      16,
    );

    stream.end();
  });

  /**
   * The way in that used to be broken: a conversation opened from the thread
   * list, so the thread id is known on the first paint and never changes.
   *
   * `useScrollToBottom()` (`src/components/workspace/mobile/use-scroll-to-bottom.ts`)
   * used to resolve the transcript element from a single
   * `requestAnimationFrame` in an effect keyed on `[resetKey, rootRef]`. Here
   * that frame lands while `MessageList` is still painting
   * `MessageListSkeleton`, so `findTranscriptScroller()` returns null and — the
   * thread id never changing — the effect never runs again: no scroll listener,
   * `isAtBottom` stuck at `true`, the affordance unreachable for the life of
   * the page. The hook now watches the surface for the skeleton→transcript
   * swap instead of probing once, so no scroll gymnastics are needed to reach
   * it.
   *
   * Keep this test cold: opening the same transcript via `/chats/new` + send
   * makes the thread id change mid-page, which is exactly the path that
   * masked the defect (the sibling test above).
   */
  test("an existing conversation loaded cold also offers back-to-bottom", async ({
    page,
  }) => {
    const thread: MockThread = {
      thread_id: MOCK_THREAD_ID,
      title: "A long conversation",
      messages: longTranscript(24),
    };
    mockLangGraphAPI(page, { threads: [thread] });
    await mockModels(page);
    await page.goto(CHAT_PATH);
    await openComposer(page);
    await expect(page.getByText("Answer number 23")).toBeVisible({
      timeout: 15_000,
    });

    const transcript = scroller(page);
    await expect
      .poll(
        () =>
          transcript.evaluate((element) =>
            Math.round(
              element.scrollHeight - element.scrollTop - element.clientHeight,
            ),
          ),
        { timeout: 15_000 },
      )
      .toBeLessThanOrEqual(16);

    // The scroller starts at the bottom, so the affordance must be absent:
    // the assertion below is about a real scroll-away, not about a hook that
    // never binds.
    await expect(scrollBottomButton(page)).toHaveCount(0);

    await transcript.evaluate((element) => {
      element.scrollTop = 0;
    });

    await expect(scrollBottomButton(page)).toBeVisible({ timeout: 5_000 });
  });
});
