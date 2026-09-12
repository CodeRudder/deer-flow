import { devices, expect, test, type Page } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

/**
 * Mobile artifact full-screen view (T6, prototype ⑤).
 *
 * Same shape as `mobile-chat.spec.ts`: the iPhone 13 preset supplies the
 * User-Agent the middleware branches on (`defaultBrowserType` is forced back to
 * chromium because the iPhone presets are WebKit-only and this suite installs
 * chromium only), and every backend endpoint is answered by `page.route()`.
 *
 * Two endpoints matter beyond `mockLangGraphAPI`:
 * - the thread's *state*, which the screen reads for the artifact list and for
 *   the transcript a `write-file:` draft is built from;
 * - the artifact bytes themselves, one stub per render mode.
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const CHAT_PATH = `/workspace/chats/${MOCK_THREAD_ID}`;

const HTML_PATH = "/artifact-fixtures/report.html";
const IMAGE_PATH = "/artifact-fixtures/chart.png";
const JSON_PATH = "/artifact-fixtures/data.json";

const HTML_BODY =
  "<!doctype html><html><head><title>Report</title></head><body><h1>Report body</h1></body></html>";
const JSON_BODY = '{\n  "status": "draft",\n  "count": 3\n}';
/** A 1x1 PNG — enough for the `<img>` to actually load. */
const PNG_BODY = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

/** The public artifact URL, exactly as the app builds it. */
function artifactPath(filepath: string) {
  return `${CHAT_PATH}/artifacts/${encodeURIComponent(filepath)}`;
}

const ARTIFACT_THREAD = {
  thread_id: MOCK_THREAD_ID,
  title: "Has artifacts",
  artifacts: [HTML_PATH, IMAGE_PATH, JSON_PATH],
};

/**
 * Serves the artifact bytes for every path the screen may fetch.
 *
 * Matched with a regex rather than a glob: the download link carries
 * `?download=true`, and a trailing `**` glob is easy to get wrong across a
 * query string.
 */
function mockArtifactBytes(page: Page) {
  return page.route(/\/api\/threads\/[^/]+\/artifacts\//, (route) => {
    const url = route.request().url();
    if (url.includes(".png")) {
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        body: PNG_BODY,
      });
    }
    if (url.includes(".json")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON_BODY,
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: HTML_BODY,
    });
  });
}

// ---------------------------------------------------------------------------
// Entry point: the 产物 row in the ⋯ sheet
// ---------------------------------------------------------------------------

test.describe("Mobile artifact entry point", () => {
  test("the ⋯ sheet carries 产物 only when the thread has artifacts", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await page.goto(CHAT_PATH);
    await expect(page.getByTestId("mobile-chat-more")).toBeVisible({
      timeout: 15_000,
    });

    await page.getByTestId("mobile-chat-more").click();
    await expect(page.getByTestId("mobile-chat-menu")).toBeVisible();
    await expect(page.getByTestId("mobile-chat-artifacts")).toBeVisible();
  });

  test("a thread without artifacts has no 产物 row", async ({ page }) => {
    mockLangGraphAPI(page, {
      threads: [{ thread_id: MOCK_THREAD_ID, title: "No artifacts" }],
    });
    await page.goto(CHAT_PATH);
    await expect(page.getByTestId("mobile-chat-more")).toBeVisible({
      timeout: 15_000,
    });

    await page.getByTestId("mobile-chat-more").click();
    await expect(page.getByTestId("mobile-chat-menu")).toBeVisible();
    await expect(page.getByTestId("mobile-chat-artifacts")).toHaveCount(0);
  });

  test("opening it lands on the public artifact URL, never on /m/", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(CHAT_PATH);
    await expect(page.getByTestId("mobile-chat-more")).toBeVisible({
      timeout: 15_000,
    });

    await page.getByTestId("mobile-chat-more").click();
    await page.getByTestId("mobile-chat-artifacts").click();

    await expect(page.getByTestId("mobile-artifact-title")).toBeVisible({
      timeout: 15_000,
    });

    const url = new URL(page.url());
    expect(url.pathname.startsWith(`${CHAT_PATH}/artifacts/`)).toBe(true);
    // Plan §1.2.1: the middleware rewrites server-side, so the internal prefix
    // must never surface in the address bar.
    expect(url.pathname).not.toContain("/m/");
    // The path travels as one encoded segment; decoded it is the artifact.
    expect(decodeURIComponent(url.pathname.split("/artifacts/")[1] ?? "")).toBe(
      HTML_PATH,
    );
    await expect(page.getByTestId("mobile-artifact-title")).toHaveText(
      "report.html",
    );
  });
});

// ---------------------------------------------------------------------------
// Entry point: a write_file step in the transcript
// ---------------------------------------------------------------------------

const DRAFT_PATH = "/artifact-fixtures/draft.md";
/** `artifact-preview.spec.ts`'s fixture shape: an in-progress write. */
const DRAFT_MESSAGES = [
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
          content: "# Draft title\n\nDraft body text",
        },
      },
    ],
  },
];

test.describe("Mobile transcript entry point", () => {
  test("tapping a write_file step opens the draft full-screen", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Drafting",
          messages: DRAFT_MESSAGES,
          artifacts: [],
        },
      ],
    });
    await page.goto(CHAT_PATH);

    // The step is the desktop's artifact entry point; on mobile the same
    // `select()` + `setOpen(true)` pair has to become a navigation, because
    // there is no panel to open.
    const step = page.getByText(DRAFT_PATH);
    await expect(step).toBeVisible({ timeout: 15_000 });
    await step.click();

    await expect(page.getByTestId("mobile-artifact-title")).toBeVisible({
      timeout: 15_000,
    });
    expect(new URL(page.url()).pathname).not.toContain("/m/");
    await expect(page.getByTestId("mobile-artifact-title")).toHaveText(
      "draft.md",
    );

    // The draft is not a file on the backend — it only exists in the tool
    // call's arguments — so this also proves the screen gets the transcript.
    await expect(page.getByText("Draft title")).toBeVisible({
      timeout: 15_000,
    });
    // A draft has no URL to fetch, so the bar offers only the renderer's own
    // controls, never a download.
    await expect(page.getByTestId("mobile-artifact-download")).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// Render modes
// ---------------------------------------------------------------------------

test.describe("Mobile artifact render modes", () => {
  test.describe("html", () => {
    test("renders the shared sandboxed preview iframe", async ({ page }) => {
      mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
      await mockArtifactBytes(page);
      await page.goto(artifactPath(HTML_PATH));

      const frame = page.locator('iframe[title="Artifact preview"]');
      await expect(frame).toBeVisible({ timeout: 15_000 });
      // The sandbox attribute is what keeps agent-written markup away from the
      // app; the src is a blob built from the fetched bytes (base-href and
      // scroll restoration injected by the shared preview).
      await expect(frame).toHaveAttribute(
        "sandbox",
        "allow-scripts allow-forms",
      );
      await expect
        .poll(async () => (await frame.getAttribute("src")) ?? "", {
          timeout: 15_000,
        })
        .toMatch(/^blob:/);
    });
  });

  test.describe("image", () => {
    test("renders the image and opens the lightbox on tap", async ({
      page,
    }) => {
      mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
      await mockArtifactBytes(page);
      await page.goto(artifactPath(IMAGE_PATH));

      const image = page.getByTestId("mobile-artifact-image");
      await expect(image).toBeVisible({ timeout: 15_000 });
      await expect(image).toHaveAttribute("alt", "chart.png");
      // Loaded, not merely present.
      await expect
        .poll(async () =>
          image.evaluate(
            (element) => (element as HTMLImageElement).naturalWidth,
          ),
        )
        .toBeGreaterThan(0);

      await image.click();
      const lightbox = page.getByRole("dialog");
      await expect(lightbox).toBeVisible();
      // The reused desktop lightbox is 32px per control; the mobile page marks
      // the body so `artifact-surface.css` can grow them to the touch floor.
      const button = lightbox.getByRole("button", { name: "Close" });
      const box = await button.boundingBox();
      expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    });
  });

  test.describe("code", () => {
    test("renders the source and toggles wrapping", async ({ page }) => {
      mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
      await mockArtifactBytes(page);
      await page.goto(artifactPath(JSON_PATH));

      const code = page.getByTestId("mobile-artifact-code");
      await expect(code).toBeVisible({ timeout: 15_000 });
      await expect(code).toContainText('"status": "draft"');
      // Long lines scroll horizontally until the wrap toggle is used.
      await expect(code).toHaveAttribute("data-wrap", "false");
      await expect(code).toHaveCSS("white-space", "pre");

      await page.getByTestId("mobile-artifact-wrap").click();
      await expect(code).toHaveAttribute("data-wrap", "true");
      await expect(code).toHaveCSS("white-space", "pre-wrap");
    });
  });

  test("an unpreviewable file offers download only", async ({ page }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Archive",
          artifacts: ["/artifact-fixtures/bundle.zip"],
        },
      ],
    });

    await page.goto(artifactPath("/artifact-fixtures/bundle.zip"));

    await expect(page.getByTestId("mobile-artifact-download-only")).toBeVisible(
      { timeout: 15_000 },
    );
    // No renderer at all, but the download action is still there.
    await expect(page.getByTestId("mobile-artifact-code")).toHaveCount(0);
    await expect(page.getByTestId("mobile-artifact-download")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Actions and navigation
// ---------------------------------------------------------------------------

test.describe("Mobile artifact actions", () => {
  test("the download action fires at the artifact with ?download=true", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(artifactPath(HTML_PATH));

    const download = page.getByTestId("mobile-artifact-download");
    await expect(download).toBeVisible({ timeout: 15_000 });
    await expect(download).toHaveAttribute(
      "href",
      /\/api\/threads\/[^/]+\/artifacts\/artifact-fixtures\/report\.html\?download=true$/,
    );

    // The action opens the download in a new tab; a context-level route keeps
    // it from reaching a backend that is not running here.
    let requested = false;
    await page.context().route(/download=true/, (route) => {
      requested = true;
      return route.fulfill({
        status: 200,
        contentType: "text/plain",
        body: "ok",
      });
    });

    const [popup] = await Promise.all([
      page.waitForEvent("popup"),
      download.click(),
    ]);
    await expect.poll(() => requested).toBe(true);
    expect(popup.url()).toContain("download=true");
    await popup.close();
  });

  test("every action-bar control clears the touch floor", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(artifactPath(JSON_PATH));

    const bar = page.getByTestId("mobile-artifact-actions");
    await expect(bar).toBeVisible({ timeout: 15_000 });

    const boxes = await bar.locator("a, button").evaluateAll((elements) =>
      elements.map((element) => {
        const rect = element.getBoundingClientRect();
        return { height: rect.height, width: rect.width };
      }),
    );
    expect(boxes.length).toBeGreaterThan(0);
    for (const box of boxes) {
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
  });

  test("back returns to the chat", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(artifactPath(HTML_PATH));

    const back = page.getByTestId("mobile-artifact-back");
    await expect(back).toBeVisible({ timeout: 15_000 });
    // The public chat path, so the middleware re-lands it on the mobile tree.
    await expect(back).toHaveAttribute("href", CHAT_PATH);

    await back.click();
    await expect(page.getByTestId("mobile-composer")).toBeVisible({
      timeout: 15_000,
    });
    expect(new URL(page.url()).pathname).toBe(CHAT_PATH);
  });

  test("the tab bar gives way to the action bar", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(artifactPath(HTML_PATH));

    await expect(page.getByTestId("mobile-artifact-actions")).toBeVisible({
      timeout: 15_000,
    });
    // The screen is full-bleed: one bottom bar, not two (prototype ⑤).
    await expect(page.getByRole("navigation")).toHaveCount(1);
  });
});

// ---------------------------------------------------------------------------
// The artifact list
// ---------------------------------------------------------------------------

test.describe("Mobile artifact switching", () => {
  test("the thread's other artifacts are one tap away", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(artifactPath(HTML_PATH));

    const tabs = page.getByTestId("mobile-artifact-tabs");
    await expect(tabs).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("mobile-artifact-title")).toHaveText(
      "report.html",
    );

    await tabs.getByRole("link", { name: "data.json" }).click();

    await expect(page.getByTestId("mobile-artifact-code")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("mobile-artifact-title")).toHaveText(
      "data.json",
    );
    expect(new URL(page.url()).pathname).not.toContain("/m/");
  });
});
