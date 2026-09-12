import { devices, expect, test, type Page, type Route } from "@playwright/test";

import {
  handleJoinedRunStream,
  JOINED_RUN_REPLY,
  MOCK_THREAD_ID,
  mockLangGraphAPI,
  type MockThread,
} from "./utils/mock-api";

/**
 * Adopting a run that is already in flight when a chat page opens.
 *
 * `useThreadStream()` reads the thread's run list on open and, when the newest
 * run is still `pending`/`running`, calls `thread.joinStream(runId)` for it
 * (`pickActiveRunId` + the adoption effect in `src/core/threads/hooks.ts`). That
 * is what a thread opened from another device — or after this browser was
 * closed, so it has no `sessionStorage` reconnect key — looks like: without the
 * adoption the page sits on a stale snapshot with an idle composer.
 *
 * The observable consequences are exactly two, and this spec asserts both:
 * the composer starts in its stop state, and the run's remaining output streams
 * into the transcript without the user sending anything.
 *
 * Same setup as the other mobile specs: the iPhone 13 preset supplies the
 * User-Agent the middleware branches on (`defaultBrowserType` is forced back to
 * chromium because the iPhone presets are WebKit-only and this suite installs
 * chromium only), and `mockLangGraphAPI` answers every backend endpoint — here
 * with `activeRun` declared on the fixture thread, which is what makes the runs
 * list report a live run instead of the settled `success` one.
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const CHAT_PATH = `/workspace/chats/${MOCK_THREAD_ID}`;
const DESKTOP_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";

/** The run the fixture thread reports as in flight. */
const ACTIVE_RUN_ID = "00000000-0000-0000-0000-0000000000aa";

/** How long the join response is withheld — see `holdJoinStream`. */
const HOLD_MS = 5_000;

const HUMAN_MESSAGE = {
  type: "human",
  id: "msg-human-1",
  content: [{ type: "text", text: "Start the long answer" }],
};

const EARLIER_REPLY = {
  type: "ai",
  id: "msg-ai-earlier",
  content: "Still working on it…",
};

/** A thread whose newest run is `running`, with two turns already archived. */
const ACTIVE_THREAD: MockThread = {
  thread_id: MOCK_THREAD_ID,
  title: "Already running",
  messages: [HUMAN_MESSAGE, EARLIER_REPLY],
  activeRun: { run_id: ACTIVE_RUN_ID, status: "running" },
};

const JOIN_STREAM_URL = /\/api\/langgraph\/threads\/[^/]+\/runs\/[^/]+\/stream/;

/**
 * Answer the join request from the canned mock, but withhold the body for
 * `HOLD_MS` first.
 *
 * The composer's stop state is only observable while the stream is open, and
 * `joinStream` flips `isLoading` before the request is even issued — so holding
 * the response is what makes the assertion deterministic instead of a race
 * against a body that lands in one tick. `page.route()` cannot hold a body open
 * at all (see the loopback server in `mobile-streaming.spec.ts` for that
 * problem); withholding the handler's own reply can, and needs no server.
 */
function holdJoinStream(page: Page, holdMs: number = HOLD_MS) {
  return page.route(JOIN_STREAM_URL, async (route: Route) => {
    await new Promise((resolve) => setTimeout(resolve, holdMs));
    return handleJoinedRunStream(route, ACTIVE_THREAD);
  });
}

/** Every request the page made to the given URL fragment, in order. */
function recordRequests(page: Page, predicate: (url: string) => boolean) {
  const urls: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (predicate(url)) {
      urls.push(url);
    }
  });
  return urls;
}

function sendButton(page: Page) {
  return page.getByTestId("mobile-composer-send");
}

test.describe("Mobile chat adopting an active run", () => {
  test("opening a thread with a run in flight offers stop and streams the reply in", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ACTIVE_THREAD] });
    // The join URL carries `?cancel_on_disconnect=…`, so match on the path
    // fragment rather than on the end of the URL.
    const joins = recordRequests(page, (url) =>
      url.includes(`/runs/${ACTIVE_RUN_ID}/stream`),
    );
    const submits = recordRequests(page, (url) => url.endsWith("/runs/stream"));
    await holdJoinStream(page);

    await page.goto(CHAT_PATH);

    // The archived turns are on screen first, so everything asserted below is
    // attributable to the join rather than to the history fetch.
    await expect(page.getByText(EARLIER_REPLY.content)).toBeVisible({
      timeout: 15_000,
    });

    // 1. The composer is in its stop state: the page adopted the run it did not
    // start. This is the state a user sees as "the agent is still working".
    await expect(sendButton(page)).toHaveAttribute(
      "aria-label",
      "Stop generating",
      { timeout: 15_000 },
    );

    // 2. The run's remaining output streams in, without the user sending
    // anything — the whole point of joining rather than re-rendering.
    await expect(page.getByText(JOINED_RUN_REPLY)).toBeVisible({
      timeout: 20_000,
    });

    // The join is exactly what produced it: the page opened the run's own
    // stream once (the adoption effect is guarded against re-joining), and it
    // never submitted a run of its own.
    expect(joins.length).toBe(1);
    expect(joins[0]).toContain(`/runs/${ACTIVE_RUN_ID}/stream`);
    expect(submits).toEqual([]);

    // And once the run ends, the composer hands itself back.
    await expect(sendButton(page)).toHaveAttribute("aria-label", "Send", {
      timeout: 15_000,
    });
  });

  test("a thread with no run in flight keeps the idle composer", async ({
    page,
  }) => {
    // The control: the same fixture without `activeRun` — the settled `success`
    // run every other spec already gets. Nothing may be joined, so the composer
    // must be idle even though the runs list is non-empty.
    mockLangGraphAPI(page, {
      threads: [{ ...ACTIVE_THREAD, activeRun: undefined }],
    });
    const joins = recordRequests(page, (url) =>
      url.includes(`/runs/${ACTIVE_RUN_ID}/stream`),
    );
    await page.goto(CHAT_PATH);

    await expect(page.getByText(EARLIER_REPLY.content)).toBeVisible({
      timeout: 15_000,
    });
    await expect(sendButton(page)).toHaveAttribute("aria-label", "Send");

    // Give a stray join a chance to appear before declaring it absent.
    await page.waitForTimeout(2_000);
    expect(joins).toEqual([]);
  });

  test.describe("desktop browser", () => {
    test.use({
      userAgent: DESKTOP_UA,
      viewport: { width: 1280, height: 800 },
      isMobile: false,
      hasTouch: false,
    });

    test("the desktop composer adopts the run too", async ({ page }) => {
      mockLangGraphAPI(page, { threads: [ACTIVE_THREAD] });
      await holdJoinStream(page);
      await page.goto(CHAT_PATH);

      // The desktop's send control carries a fixed `aria-label="Submit"` and
      // swaps its icon for the stop square while streaming
      // (`PromptInputSubmit` in `components/ai-elements/prompt-input.tsx`), so
      // the icon is the observable that distinguishes stop from send.
      const submit = page.getByRole("button", { name: "Submit" });
      await expect(submit.locator("svg.lucide-square")).toBeVisible({
        timeout: 15_000,
      });

      await expect(page.getByText(JOINED_RUN_REPLY)).toBeVisible({
        timeout: 20_000,
      });
      await expect(submit.locator("svg.lucide-arrow-up")).toBeVisible({
        timeout: 15_000,
      });
    });
  });
});
