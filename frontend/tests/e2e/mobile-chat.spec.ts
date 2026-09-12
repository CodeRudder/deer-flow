import { devices, expect, test, type Page, type Route } from "@playwright/test";

import {
  MOCK_RUN_ID,
  MOCK_THREAD_ID,
  mockLangGraphAPI,
  type MockThread,
} from "./utils/mock-api";

/**
 * Mobile chat page (T5, prototype ②③).
 *
 * Same shape as `mobile-threads.spec.ts`: the iPhone 13 preset supplies the
 * User-Agent the middleware branches on (`defaultBrowserType` is forced back to
 * chromium because the iPhone presets are WebKit-only and this suite installs
 * chromium only), and every backend endpoint is answered by `page.route()`.
 *
 * The chat page needs more of the API than the thread list did, so on top of
 * `mockLangGraphAPI` this file adds:
 * - `/api/models` with a model that *supports thinking*, because the mode and
 *   reasoning-effort controls are hidden for a model that cannot think, and the
 *   default mock returns no models at all;
 * - a `/runs/stream` handler that records the outgoing request body (the mode
 *   assertions read it) and returns the transcript the test wants.
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const IPHONE_UA = devices["iPhone 13"].userAgent;
const DESKTOP_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";

const CHAT_PATH = `/workspace/chats/${MOCK_THREAD_ID}`;

const THINKING_MODEL = {
  id: "mock-model",
  name: "mock-model",
  model: "mock-model",
  display_name: "Mock Model",
  // Both flags matter: without `supports_thinking` the mode menu collapses to
  // Flash, and without `supports_reasoning_effort` the 推理强度 row is hidden.
  supports_thinking: true,
  supports_reasoning_effort: true,
};

/**
 * A second chat model, so 模型 has somewhere to go. It stays *after*
 * `THINKING_MODEL` so the first entry — the one the composer pins as the
 * default — is unchanged for every other test in this file.
 */
const SECOND_MODEL = {
  id: "mock-model-2",
  name: "mock-model-2",
  model: "mock-model-2",
  display_name: "Mock Model 2",
  supports_thinking: true,
  supports_reasoning_effort: true,
};

/**
 * A vision model, which is what makes the picker's chat/vision toggle render
 * at all (`visionModels.length > 0`). It is a separate selection from
 * `model_name`, so the two are asserted independently.
 */
const VISION_MODEL = {
  id: "mock-vision",
  name: "mock-vision",
  model: "mock-vision",
  display_name: "Mock Vision",
  is_default: true,
};

type StreamedMessage = Record<string, unknown>;

function mockModels(page: Page) {
  // Registered after `mockLangGraphAPI`, and Playwright matches the most
  // recently registered handler first, so this wins over the empty list.
  return page.route("**/api/models", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        models: [THINKING_MODEL, SECOND_MODEL],
        vision_models: [VISION_MODEL],
        token_usage: { enabled: false },
      }),
    }),
  );
}

function sse(messages: StreamedMessage[]) {
  return (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      // The SDK reads the created run's thread id from `Content-Location`, not
      // from the SSE `metadata` event. Without it `onCreated` never fires, so
      // the post-create `history.replaceState` — the code path this suite cares
      // about — is never exercised.
      headers: {
        "Content-Location": `/threads/${MOCK_THREAD_ID}/runs/${MOCK_RUN_ID}`,
      },
      body: [
        {
          event: "metadata",
          data: { run_id: MOCK_RUN_ID, thread_id: MOCK_THREAD_ID },
        },
        { event: "values", data: { messages } },
        { event: "end", data: {} },
      ]
        .map(
          (event) =>
            `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`,
        )
        .join(""),
    });
}

const HUMAN_MESSAGE = {
  type: "human",
  id: "msg-human-1",
  content: [{ type: "text", text: "Hello" }],
};

const AI_REPLY = {
  type: "ai",
  id: "msg-ai-1",
  content: "Hello from DeerFlow!",
};

/** Records the JSON body of every run-stream request. */
function recordStreamBodies(page: Page) {
  const bodies: Record<string, unknown>[] = [];
  void page.route("**/runs/stream", (route) => {
    bodies.push(route.request().postDataJSON() as Record<string, unknown>);
    return sse([HUMAN_MESSAGE, AI_REPLY])(route);
  });
  return bodies;
}

function textarea(page: Page) {
  return page.getByTestId("mobile-composer").locator("textarea");
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

test.describe("Mobile chat routing", () => {
  test("a mobile UA is rewritten onto the mobile chat page", async ({
    request,
  }) => {
    const response = await request.get(CHAT_PATH, {
      headers: { "user-agent": IPHONE_UA },
      maxRedirects: 0,
    });

    // The browser URL stays `/workspace/chats/...` while `/m/...` renders.
    expect(response.headers()["x-middleware-rewrite"]).toBe(`/m${CHAT_PATH}`);
  });

  test("a desktop UA gets the desktop page, not the mobile one", async ({
    request,
  }) => {
    const response = await request.get(CHAT_PATH, {
      headers: { "user-agent": DESKTOP_UA },
      maxRedirects: 0,
    });

    expect(response.headers()["x-middleware-rewrite"]).toBeUndefined();
  });

  test.describe("desktop browser", () => {
    test.use({
      userAgent: DESKTOP_UA,
      viewport: { width: 1280, height: 800 },
      isMobile: false,
      hasTouch: false,
    });

    test("renders the desktop composer and no mobile composer", async ({
      page,
    }) => {
      mockLangGraphAPI(page, { threads: [] });
      await page.goto("/workspace/chats/new");

      // The desktop input box.
      await expect(page.getByPlaceholder(/how can i assist you/i)).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByTestId("mobile-composer")).toHaveCount(0);
    });
  });
});

// ---------------------------------------------------------------------------
// Composer main row
// ---------------------------------------------------------------------------

test.describe("Mobile chat composer", () => {
  test("the main row is only ＋ / input / send, and the input is not focused", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    await page.goto("/workspace/chats/new");

    const composer = page.getByTestId("mobile-composer");
    await expect(composer).toBeVisible({ timeout: 15_000 });

    // ＋, the mode pill, the model pill and send. T16 moved mode and model onto
    // the main row as pills — the ＋-panel test further down asserts the sheet
    // no longer carries them — so the row is four controls, not two.
    await expect(composer.locator("button")).toHaveCount(4);
    await expect(page.getByTestId("mobile-composer-plus")).toBeVisible();
    await expect(page.getByTestId("mobile-composer-send")).toBeVisible();
    await expect(composer.locator("textarea")).toHaveCount(1);

    // autoFocus off: the keyboard must not cover the transcript on open.
    await expect(textarea(page)).not.toBeFocused();
  });

  test("the ＋ panel carries the four attachments entries and the six desktop controls", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    await page.goto("/workspace/chats/new");

    await page.getByTestId("mobile-composer-plus").click();

    const sheet = page.getByTestId("mobile-composer-sheet");
    await expect(sheet).toBeVisible();

    for (const id of ["gallery", "camera", "file", "thinking"]) {
      await expect(
        page.getByTestId(`mobile-composer-sheet-${id}`),
      ).toBeVisible();
    }
    // 图片生成模型 / 视频生成模型 / 推理强度 / 计划模式 — the controls the main row
    // cannot hold. Mode and model used to be rows here too; T16 moved them onto
    // the main row as pills, and this asserts they are gone rather than merely
    // hidden, so a half-finished migration fails here.
    for (const id of ["image", "video", "effort", "plan"]) {
      await expect(
        page.getByTestId(`mobile-composer-sheet-${id}`),
      ).toBeVisible();
    }
    for (const id of ["model", "mode"]) {
      await expect(page.getByTestId(`mobile-composer-sheet-${id}`)).toHaveCount(
        0,
      );
    }

    // The pills name what the request will actually carry: nothing is pinned
    // yet, so the first configured model stands in, and the mode follows that
    // model's thinking capability (Pro for a thinking model).
    await expect(page.getByTestId("mobile-composer-model-pill")).toContainText(
      "Mock Model",
    );
    await expect(page.getByTestId("mobile-composer-mode-pill")).toContainText(
      "Pro",
    );
  });

  test("picking a chat model relabels the pill and reaches the outgoing request", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    const bodies = recordStreamBodies(page);
    await page.goto("/workspace/chats/new");
    await expect(page.getByTestId("mobile-composer")).toBeVisible({
      timeout: 15_000,
    });

    // The pill is the entry point now — no `＋` panel in between.
    await page.getByTestId("mobile-composer-model-pill").click();

    // The picker is the desktop dialog, portalled above the composer.
    const dialog = page.getByTestId("mobile-composer-model-mock-model-2");
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    await dialog.click();

    // Selecting closes the dialog and the pill reports the new choice.
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId("mobile-composer-model-pill")).toContainText(
      "Mock Model 2",
    );

    await textarea(page).fill("Hello");
    await page.getByTestId("mobile-composer-send").click();

    await expect
      .poll(() => bodies.length, { timeout: 10_000 })
      .toBeGreaterThan(0);
    const context = bodies[0]?.context as Record<string, unknown>;
    expect(context.model_name).toBe("mock-model-2");
  });

  test("the vision category pins vision_model_name independently of model_name", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    const bodies = recordStreamBodies(page);
    await page.goto("/workspace/chats/new");
    await expect(page.getByTestId("mobile-composer")).toBeVisible({
      timeout: 15_000,
    });

    await page.getByTestId("mobile-composer-model-pill").click();
    await page.getByTestId("mobile-composer-model-category-vision").click();

    const visionOption = page.getByTestId(
      "mobile-composer-vision-model-mock-vision",
    );
    await expect(visionOption).toBeVisible();
    // The chat list is gone, but the chat selection is not: the two are
    // separate context fields.
    await expect(
      page.getByTestId("mobile-composer-model-mock-model-2"),
    ).toHaveCount(0);
    await visionOption.click();

    // Wait for the dialog to actually unmount before typing: it keeps its
    // dismissable layer mounted for the exit animation, and that layer would
    // swallow the first click.
    await expect(visionOption).toHaveCount(0);

    await textarea(page).fill("Hello");
    await page.getByTestId("mobile-composer-send").click();

    await expect
      .poll(() => bodies.length, { timeout: 10_000 })
      .toBeGreaterThan(0);
    const context = bodies[0]?.context as Record<string, unknown>;
    expect(context.vision_model_name).toBe("mock-vision");
    expect(context.model_name).toBe("mock-model");
  });

  test("the thinking cell toggles the same state the mode submenu writes", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    await page.goto("/workspace/chats/new");

    await page.getByTestId("mobile-composer-plus").click();
    const thinking = page.getByTestId("mobile-composer-sheet-thinking");
    // Default mode for a thinking-capable model is Pro, which thinks.
    await expect(thinking).toHaveAttribute("aria-pressed", "true");

    await thinking.click();
    await expect(thinking).toHaveAttribute("aria-pressed", "false");
  });

  test("picking a mode from the pill reaches the outgoing request", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    const bodies = recordStreamBodies(page);
    await page.goto("/workspace/chats/new");
    await expect(page.getByTestId("mobile-composer")).toBeVisible({
      timeout: 15_000,
    });

    await page.getByTestId("mobile-composer-mode-pill").click();
    await page.getByTestId("mobile-composer-mode-thinking").click();
    // Selecting closes the layer and the pill relabels in place.
    await expect(page.getByTestId("mobile-composer-mode-sheet")).toHaveCount(0);
    await expect(page.getByTestId("mobile-composer-mode-pill")).toContainText(
      "Reasoning",
    );

    await textarea(page).fill("Hello");
    await page.getByTestId("mobile-composer-send").click();

    await expect
      .poll(() => bodies.length, { timeout: 10_000 })
      .toBeGreaterThan(0);
    const context = bodies[0]?.context as Record<string, unknown>;
    // The default (Pro) would send is_plan_mode: true; 思考 must not.
    expect(context.thinking_enabled).toBe(true);
    expect(context.is_plan_mode).toBe(false);
    expect(context.reasoning_effort).toBe("low");
    // The chosen mode is carried verbatim.
    expect(context.mode).toBe("thinking");
  });

  test("sending a message posts it and renders the mocked reply", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    const bodies = recordStreamBodies(page);
    await page.goto("/workspace/chats/new");
    await expect(page.getByTestId("mobile-composer")).toBeVisible({
      timeout: 15_000,
    });

    await textarea(page).fill("Hello");
    await page.getByTestId("mobile-composer-send").click();

    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
    await expect.poll(() => bodies.length, { timeout: 10_000 }).toBe(1);

    // The post-create navigation uses `history.replaceState`, which issues no
    // request and therefore never passes through the middleware — it must write
    // the public path itself. `/m/` must never reach the address bar.
    expect(new URL(page.url()).pathname).toBe(CHAT_PATH);
    expect(page.url()).not.toContain("/m/");
  });

  test("the transcript keeps message actions visible without hover", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Existing chat",
          messages: [HUMAN_MESSAGE, AI_REPLY],
        },
      ],
    });
    await mockModels(page);
    await page.goto(CHAT_PATH);

    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 15_000,
    });

    // The desktop reveals these bars on `group-hover` only; on touch that means
    // never, so the mobile surface forces them visible (see chat-surface.css).
    const bars = page.locator(
      '[class*="group-hover/assistant-turn:opacity-100"], [class*="group-hover/conversation-message:opacity-100"]',
    );
    await expect(bars.first()).toBeAttached();
    const opacities = await bars.evaluateAll((elements) =>
      elements.map((element) => getComputedStyle(element).opacity),
    );
    expect(opacities.length).toBeGreaterThan(0);
    for (const opacity of opacities) {
      expect(opacity).toBe("1");
    }
  });

  test("the header back link points at the public thread list", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);
    await page.goto(CHAT_PATH);

    const back = page.getByTestId("mobile-chat-back");
    await expect(back).toBeVisible({ timeout: 15_000 });
    await expect(back).toHaveAttribute("href", "/workspace");
  });
});

// ---------------------------------------------------------------------------
// Clarification card
// ---------------------------------------------------------------------------

const CLARIFICATION_REQUEST = {
  version: 1,
  kind: "human_input_request",
  source: "ask_clarification",
  request_id: "clarification:call-1",
  tool_call_id: "call-1",
  clarification_type: "approach_choice",
  question: "Which environment?",
  input_mode: "single_choice",
  options: [
    { id: "option-1", label: "dev", value: "dev" },
    { id: "option-2", label: "staging", value: "staging" },
  ],
};

const CLARIFICATION_MESSAGES: StreamedMessage[] = [
  HUMAN_MESSAGE,
  {
    type: "ai",
    id: "msg-ai-clarify",
    content: "I need one detail first.",
    tool_calls: [
      {
        id: "call-1",
        name: "ask_clarification",
        args: { question: "Which environment?" },
      },
    ],
  },
  {
    type: "tool",
    id: "msg-tool-clarify",
    name: "ask_clarification",
    tool_call_id: "call-1",
    content: "Which environment?",
    artifact: { human_input: CLARIFICATION_REQUEST },
  },
];

/** The transcript after the answer is accepted, as the backend would report it. */
const ANSWERED_MESSAGES: StreamedMessage[] = [
  ...CLARIFICATION_MESSAGES,
  {
    type: "human",
    id: "msg-human-answer",
    content: [{ type: "text", text: "dev" }],
    additional_kwargs: {
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
    },
  },
];

test.describe("Mobile clarification card", () => {
  const CLARIFY_THREAD: MockThread = {
    thread_id: MOCK_THREAD_ID,
    title: "Needs a decision",
    messages: CLARIFICATION_MESSAGES,
  };

  test("a pending card locks the composer, and answering unlocks it", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [CLARIFY_THREAD] });
    await mockModels(page);

    // The transcript is re-read from history after the answer is posted (the
    // send pipeline refreshes it), so the history mock has to reflect the
    // answer once the stream has been hit — otherwise the un-answered fixture
    // would put the card straight back.
    let answered = false;
    void page.route("**/api/langgraph/threads/*/history", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            values: {
              title: CLARIFY_THREAD.title,
              messages: answered ? ANSWERED_MESSAGES : CLARIFICATION_MESSAGES,
              artifacts: [],
            },
            next: [],
            metadata: {},
            created_at: "2025-01-01T00:00:00Z",
            parent_config: null,
          },
        ]),
      }),
    );
    void page.route("**/runs/stream", (route) => {
      answered = true;
      return sse(ANSWERED_MESSAGES)(route);
    });

    await page.goto(CHAT_PATH);

    const card = page.getByTestId("human-input-card");
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toContainText("Which environment?");

    // Strict mode: the card is the only way to answer.
    await expect(textarea(page)).toBeDisabled();

    await page.getByRole("button", { name: "dev", exact: true }).click();

    // The answered response arrives through the transcript; the card swaps for
    // the compact answered row and the composer unlocks.
    await expect(page.getByTestId("human-input-card")).toHaveCount(0, {
      timeout: 10_000,
    });
    await expect(textarea(page)).toBeEnabled();
  });
});
