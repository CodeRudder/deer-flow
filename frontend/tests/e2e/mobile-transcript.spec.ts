import { devices, expect, test, type Page, type Route } from "@playwright/test";

import {
  MOCK_RUN_ID,
  MOCK_THREAD_ID,
  mockLangGraphAPI,
  type MockThread,
} from "./utils/mock-api";

/**
 * Mobile transcript content (T13, `FEATURE_LIST.md` C6 / C10, gap G9).
 *
 * The mobile chat page reuses the desktop transcript, so the *content* of a
 * turn — tool-call steps, tables, code blocks, follow-up chips — was the one
 * layer nobody had adapted. This spec pins the three pieces that changed:
 *
 * - C6: a tool-call turn opens as one "ran N steps · M tools" row and expands
 *   into the usual detail. `MessageGroup` takes an optional `collapsedSteps`
 *   prop for it; the last describe block proves the desktop is unaffected.
 * - C10: the follow-up chips the desktop paints above its input box are
 *   requested by the mobile composer too, and their row scrolls sideways
 *   instead of wrapping.
 * - The long-markdown case: no page-level horizontal scroll, with the table
 *   and the code block scrolling inside their own boxes.
 *
 * Every backend call is answered by `page.route()` — same shape as the sibling
 * `mobile-*.spec.ts` files.
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const DESKTOP_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";

const CHAT_PATH = `/workspace/chats/${MOCK_THREAD_ID}`;
/** Same draft path `mobile-artifacts.spec.ts` opens from the transcript. */
const DRAFT_PATH = "/artifact-fixtures/draft.md";

const MODEL = {
  id: "mock-model",
  name: "mock-model",
  model: "mock-model",
  display_name: "Mock Model",
  supports_thinking: true,
  supports_reasoning_effort: true,
};

/** One unbreakable run of characters — nothing the layout can wrap on. */
const UNBREAKABLE = "wide-token-".repeat(24);

const WIDE_MARKDOWN = [
  "Here is the report.",
  "",
  "| Column | Value |",
  "| --- | --- |",
  `| Notes | ${UNBREAKABLE} |`,
  "",
  "```text",
  `const value = "${UNBREAKABLE}";`,
  "```",
].join("\n");

/**
 * Three tool calls plus one reasoning trace, so the summary has something to
 * count: `steps.length` is 4 (3 tool calls + 1 reasoning) and the tool count 3.
 * `reasoning_content` has to live in `additional_kwargs` — that is the shape
 * the backend sends and the shape `extractReasoningContentFromMessage` reads.
 */
const TOOL_CALLS = [
  {
    name: "bash",
    id: "call-bash",
    args: { description: "Run the analysis script", command: "ls -la" },
  },
  {
    name: "web_search",
    id: "call-search",
    args: { query: "deerflow mobile web" },
  },
  {
    name: "read_file",
    id: "call-read",
    args: { path: "/mnt/user-data/notes.md" },
  },
];

const TRANSCRIPT = [
  {
    type: "human",
    id: "msg-human-1",
    content: [{ type: "text", text: "Do the analysis" }],
  },
  {
    type: "ai",
    id: "msg-ai-steps",
    content: "",
    additional_kwargs: { reasoning_content: "I should look at the data." },
    tool_calls: TOOL_CALLS,
  },
  ...TOOL_CALLS.map((call) => ({
    type: "tool",
    id: `msg-tool-${call.id}`,
    tool_call_id: call.id,
    content:
      call.name === "web_search"
        ? JSON.stringify([
            { url: "https://example.com/one", title: "Example one" },
          ])
        : "ok",
  })),
  { type: "ai", id: "msg-ai-final", content: WIDE_MARKDOWN },
];

const THREAD: MockThread = {
  thread_id: MOCK_THREAD_ID,
  title: "Transcript thread",
  updated_at: new Date().toISOString(),
  messages: TRANSCRIPT,
};

/** Five long suggestions: at 390px they cannot fit on one screen. */
const FOLLOWUPS = [
  "Explain how the analysis pipeline handles very long documents end to end",
  "What happens when the upload is larger than the sandbox limit",
  "Compare this result with the previous run of the same thread",
  "Show me the raw JSON the tools returned for every step",
  "Which parts of this answer came from the web search",
];

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

function mockSuggestions(page: Page) {
  void page.route("**/api/suggestions/config", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ enabled: true }),
    }),
  );
  void page.route("**/api/threads/*/suggestions", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ suggestions: FOLLOWUPS }),
    }),
  );
}

/**
 * Answers the run with the full transcript plus the new reply.
 *
 * A `values` event replaces the message list wholesale, so returning only the
 * new pair would erase the tool-call turn mid-test.
 */
function sse() {
  return (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: {
        "Content-Location": `/threads/${MOCK_THREAD_ID}/runs/${MOCK_RUN_ID}`,
      },
      body: [
        {
          event: "metadata",
          data: { run_id: MOCK_RUN_ID, thread_id: MOCK_THREAD_ID },
        },
        {
          event: "values",
          data: {
            messages: [
              ...TRANSCRIPT,
              {
                type: "human",
                id: "msg-human-2",
                content: [{ type: "text", text: "And then?" }],
              },
              { type: "ai", id: "msg-ai-2", content: "Second reply." },
            ],
          },
        },
        { event: "end", data: {} },
      ]
        .map(
          (event) =>
            `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`,
        )
        .join(""),
    });
}

async function openTranscript(page: Page) {
  mockLangGraphAPI(page, { threads: [THREAD] });
  await mockModels(page);
  mockSuggestions(page);
  await page.goto(CHAT_PATH);
  await expect(page.getByText("Here is the report.")).toBeVisible({
    timeout: 15_000,
  });
}

test.describe("mobile transcript", () => {
  test("collapses a tool-call turn into a step summary (C6)", async ({
    page,
  }) => {
    await openTranscript(page);

    const summary = page.getByTestId("mobile-collapsed-steps");
    await expect(summary).toBeVisible();
    // 4 steps (3 tool calls + the reasoning trace), 3 of them tool calls.
    await expect(summary).toContainText("Ran 4 steps");
    await expect(summary).toContainText("3 tools");
    await expect(summary).toHaveAttribute("aria-expanded", "false");

    // Collapsed means collapsed: neither the tool rows nor the reasoning row
    // is painted before the tap.
    await expect(page.getByText("Run the analysis script")).toBeHidden();
    await expect(page.getByText("I should look at the data.")).toBeHidden();
  });

  test("expands the summary into the real steps and back (C6)", async ({
    page,
  }) => {
    await openTranscript(page);

    const summary = page.getByTestId("mobile-collapsed-steps");
    await summary.click();

    await expect(summary).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText("Run the analysis script")).toBeVisible();
    await expect(
      page.getByText('Search on the web for "deerflow mobile web"'),
    ).toBeVisible();
    await expect(page.getByText("/mnt/user-data/notes.md")).toBeVisible();
    await expect(page.getByText("I should look at the data.")).toBeVisible();

    await summary.click();
    await expect(summary).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText("Run the analysis script")).toBeHidden();
  });

  test("a write_file turn stays open — its row is the draft's entry point", async ({
    page,
  }) => {
    // Collapsing this one would hide the only route to the draft screen: a
    // draft lives in the tool call's arguments, never in `values.artifacts`,
    // so the header menu cannot offer it (T6 / F7-3).
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Drafting",
          artifacts: [],
          messages: [
            {
              type: "human",
              id: "msg-human-draft",
              content: [{ type: "text", text: "Write a draft" }],
            },
            {
              type: "ai",
              id: "msg-ai-draft",
              content: "",
              tool_calls: [
                {
                  id: "call-draft",
                  name: "write_file",
                  args: {
                    description: "Writing the draft",
                    path: DRAFT_PATH,
                    content: "# Draft title",
                  },
                },
              ],
            },
          ],
        },
      ],
    });
    await mockModels(page);
    mockSuggestions(page);
    await page.goto(CHAT_PATH);

    const summary = page.getByTestId("mobile-collapsed-steps");
    await expect(summary).toBeVisible({ timeout: 15_000 });
    await expect(summary).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText(DRAFT_PATH)).toBeVisible();
  });

  test("the step summary is a 44px tap target", async ({ page }) => {
    await openTranscript(page);

    const box = await page.getByTestId("mobile-collapsed-steps").boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  });

  test("long markdown does not scroll the page sideways", async ({ page }) => {
    await openTranscript(page);
    // The code fence is highlighted by shiki before its `pre` exists.
    await expect(page.locator("pre").first()).toBeVisible({ timeout: 15_000 });

    const metrics = await page.evaluate(() => {
      const doc = document.documentElement;
      const code = document.querySelector("pre");
      const table = document.querySelector("table");
      const tableWrapper = table?.parentElement ?? null;
      if (code) {
        // The code block has to be scrollable *itself*, or the long line is
        // simply unreachable (the page must not be the scroll container).
        code.scrollLeft = 100_000;
      }
      return {
        innerWidth: window.innerWidth,
        docScrollWidth: doc.scrollWidth,
        docClientWidth: doc.clientWidth,
        codeOverflowX: code ? getComputedStyle(code).overflowX : null,
        codeScrollWidth: code?.scrollWidth ?? 0,
        codeClientWidth: code?.clientWidth ?? 0,
        codeScrollLeft: code?.scrollLeft ?? 0,
        tableOverflowX: tableWrapper
          ? getComputedStyle(tableWrapper).overflowX
          : null,
      };
    });

    expect(metrics.docScrollWidth).toBe(metrics.innerWidth);
    expect(metrics.docScrollWidth).toBe(metrics.docClientWidth);
    // Horizontally reachable, in place.
    expect(metrics.codeOverflowX).toBe("auto");
    expect(metrics.codeScrollWidth).toBeGreaterThan(metrics.codeClientWidth);
    expect(metrics.codeScrollLeft).toBeGreaterThan(0);
    // The markdown table keeps a scroll container too.
    expect(metrics.tableOverflowX).toBe("auto");
  });

  test("follow-up chips scroll sideways instead of wrapping (C10)", async ({
    page,
  }) => {
    await openTranscript(page);
    await page.route("**/api/langgraph/threads/*/runs/stream", sse());

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await textarea.fill("And then?");
    await textarea.press("Enter");

    // The chips are asked for after the run settles, so their arrival is also
    // what proves the run went through the streaming → idle edge.
    const row = page.getByTestId("mobile-followups-row");
    await expect(row).toBeVisible({ timeout: 15_000 });
    const chips = row.getByRole("button");
    await expect(chips).toHaveCount(FOLLOWUPS.length);

    const metrics = await page.evaluate(() => {
      const doc = document.documentElement;
      const rowElement = document.querySelector(
        "[data-testid='mobile-followups-row']",
      );
      const chipElements = Array.from(
        document.querySelectorAll(
          "[data-testid='mobile-followups-row'] button",
        ),
      );
      const tops = chipElements.map((chip) =>
        Math.round(chip.getBoundingClientRect().top),
      );
      return {
        innerWidth: window.innerWidth,
        docScrollWidth: doc.scrollWidth,
        rowScrollWidth: rowElement?.scrollWidth ?? 0,
        rowClientWidth: rowElement?.clientWidth ?? 0,
        rowOverflowX: rowElement
          ? getComputedStyle(rowElement).overflowX
          : null,
        chipTops: tops,
        chipCount: chipElements.length,
      };
    });

    expect(metrics.chipCount).toBe(FOLLOWUPS.length);
    // Sideways, not wrapped: every chip on the same line …
    expect(new Set(metrics.chipTops).size).toBe(1);
    // … and an actual horizontal overflow for the swipe to reveal.
    expect(metrics.rowOverflowX).toBe("auto");
    expect(metrics.rowScrollWidth).toBeGreaterThan(metrics.rowClientWidth);
    // The row must not push the page wide.
    expect(metrics.docScrollWidth).toBe(metrics.innerWidth);
  });

  test("tapping a chip fills the composer without sending", async ({
    page,
  }) => {
    await openTranscript(page);
    let streamCalls = 0;
    await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
      streamCalls += 1;
      return sse()(route);
    });

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await textarea.fill("And then?");
    await textarea.press("Enter");

    const row = page.getByTestId("mobile-followups-row");
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole("button", { name: FOLLOWUPS[0]! }).click();

    await expect(textarea).toHaveValue(FOLLOWUPS[0]!);
    await expect(row).toBeHidden();
    expect(streamCalls).toBe(1);
  });

  test("chips can be dismissed", async ({ page }) => {
    await openTranscript(page);
    await page.route("**/api/langgraph/threads/*/runs/stream", sse());

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await textarea.fill("And then?");
    await textarea.press("Enter");

    const row = page.getByTestId("mobile-followups-row");
    await expect(row).toBeVisible({ timeout: 15_000 });
    await page.getByTestId("mobile-followups-dismiss").click();
    await expect(row).toBeHidden();
  });
});

/**
 * The desktop is not supposed to notice any of this: `collapsedSteps` is
 * optional and defaults to the dense panel, and the follow-up request still
 * comes from the desktop input box.
 */
test.describe("desktop transcript", () => {
  test.use({
    userAgent: DESKTOP_UA,
    viewport: { width: 1280, height: 800 },
    isMobile: false,
    hasTouch: false,
  });

  test("keeps the dense tool panel and no collapsed summary", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [THREAD] });
    await mockModels(page);
    mockSuggestions(page);
    await page.goto(CHAT_PATH);

    await expect(page.getByText("Here is the report.")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("mobile-collapsed-steps")).toHaveCount(0);
    await expect(page.getByTestId("mobile-composer")).toHaveCount(0);
    // The desktop panel paints the last tool call directly, plus the fold for
    // the steps above it — its own wording, not the mobile summary's.
    await expect(page.getByText("/mnt/user-data/notes.md")).toBeVisible();
    await expect(page.getByText("3 more steps")).toBeVisible();
  });
});
