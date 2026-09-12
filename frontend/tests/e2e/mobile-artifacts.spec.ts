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

declare global {
  interface Window {
    __deerflowShareCalls?: { title?: string; files?: string[] }[];
  }
}

/**
 * Puts a share sheet where the browser has none (A5).
 *
 * `accepts` is the answer `canShare({ files })` gives — the whole decision the
 * action bar makes — and the recorded calls are what a real sheet would have
 * received. Both are defined on the `navigator` *instance* so they shadow the
 * platform's own: headless Chromium on macOS does expose `navigator.share`,
 * and driving the real one is not something a test can do.
 */
async function installShareApi(page: Page, accepts: boolean) {
  await page.addInitScript((acceptsFiles: boolean) => {
    window.__deerflowShareCalls = [];
    Object.defineProperty(navigator, "canShare", {
      configurable: true,
      value: () => acceptsFiles,
    });
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: (data?: ShareData) => {
        window.__deerflowShareCalls?.push({
          title: data?.title,
          files: Array.from(data?.files ?? []).map((file) => file.name),
        });
        return Promise.resolve();
      },
    });
  }, accepts);
}

/** Takes the share API away: the platform the download fallback exists for. */
async function removeShareApi(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(navigator, "canShare", {
      configurable: true,
      value: undefined,
    });
  });
}

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
// Entry point: the 文件 button in the title bar
// ---------------------------------------------------------------------------

test.describe("Mobile artifact entry point", () => {
  test("the title bar carries 文件 only when the thread has artifacts", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await page.goto(CHAT_PATH);
    await expect(page.getByTestId("mobile-chat-more")).toBeVisible({
      timeout: 15_000,
    });

    await expect(page.getByTestId("mobile-chat-files")).toBeVisible();
    // It is the header's own button now, not a row inside the ⋯ sheet — the
    // sheet would be a second entry point to the same screen.
    await expect(page.getByTestId("mobile-chat-artifacts")).toHaveCount(0);
  });

  test("a thread without artifacts has no 文件 button", async ({ page }) => {
    mockLangGraphAPI(page, {
      threads: [{ thread_id: MOCK_THREAD_ID, title: "No artifacts" }],
    });
    await page.goto(CHAT_PATH);
    await expect(page.getByTestId("mobile-chat-more")).toBeVisible({
      timeout: 15_000,
    });

    await expect(page.getByTestId("mobile-chat-files")).toHaveCount(0);
  });

  test("opening it lands on the public artifact URL, never on /m/", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(CHAT_PATH);
    await expect(page.getByTestId("mobile-chat-files")).toBeVisible({
      timeout: 15_000,
    });

    await page.getByTestId("mobile-chat-files").click();

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

  test("分享 hands the artifact's file to the platform's share sheet", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await installShareApi(page, true);
    await page.goto(artifactPath(HTML_PATH));

    const share = page.getByTestId("mobile-artifact-share");
    await expect(share).toBeVisible({ timeout: 15_000 });
    await share.click();

    // The bytes are fetched first — a share sheet takes files, not URLs — so
    // this also pins that the file, and not the session-bound link, is what
    // leaves the device.
    await expect
      .poll(() => page.evaluate(() => window.__deerflowShareCalls ?? []))
      .toEqual([{ title: "report.html", files: ["report.html"] }]);
  });

  test("分享 falls back to the download where the platform cannot share", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await removeShareApi(page);

    let requested = false;
    await page.context().route(/download=true/, (route) => {
      requested = true;
      return route.fulfill({
        status: 200,
        contentType: "text/plain",
        body: "ok",
      });
    });

    await page.goto(artifactPath(HTML_PATH));

    // A tap must never do nothing: with no share API the same button downloads.
    const [popup] = await Promise.all([
      page.waitForEvent("popup"),
      page.getByTestId("mobile-artifact-share").click({ timeout: 15_000 }),
    ]);
    await expect.poll(() => requested).toBe(true);
    expect(popup.url()).toContain("download=true");
    await popup.close();
  });

  test("刷新 re-fetches the artifact's bytes", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    let fetches = 0;
    await page.route(/\/api\/threads\/[^/]+\/artifacts\//, (route) => {
      fetches += 1;
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: HTML_BODY,
      });
    });

    await page.goto(artifactPath(HTML_PATH));

    const refresh = page.getByTestId("mobile-artifact-refresh");
    await expect(refresh).toBeVisible({ timeout: 15_000 });
    await expect.poll(() => fetches).toBeGreaterThan(0);
    const before = fetches;

    await refresh.click();

    await expect.poll(() => fetches).toBeGreaterThan(before);
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
    // The screen is full-bleed: one bottom bar, not two (prototype ⑤). The
    // action bar is a plain `div`, so the tab bar is the only `navigation`
    // landmark this screen could have — and T17 makes its absence structure
    // (the route sits in `(fullbleed)`, which does not render the bar) rather
    // than a pathname test.
    await expect(page.getByRole("navigation")).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// The artifact list
// ---------------------------------------------------------------------------

test.describe("Mobile artifact switching", () => {
  /**
   * The geometry has to be measured on a *cold start* of this route.
   *
   * The file switcher is the model dialog, whose touch rules used to live in
   * `chat-surface.css` — a stylesheet only the chat route imports. CSS is split
   * per route, so arriving here from the chat kept that chunk in memory and
   * every measurement passed, while a phone opening (or reloading) an artifact
   * link got desktop geometry instead. That is why this test starts with
   * `goto` and never with a click from the chat.
   */
  test("a cold load of the artifact URL gets the touch geometry", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(artifactPath(HTML_PATH));

    // `min-h-11` on the switcher — the title is now a control, not a heading.
    const switcher = page.getByTestId("mobile-artifact-switcher");
    await expect(switcher).toBeVisible({ timeout: 15_000 });
    await expect
      .poll(async () => (await switcher.boundingBox())?.height ?? 0)
      .toBeGreaterThanOrEqual(44);

    await switcher.click();

    // Rows are the shared 36px (`h-9`), grown by `.mobile-model-dialog
    // [role="option"]`'s min-height — which is exactly the rule that used to be
    // missing here.
    const rows = page.locator('.mobile-model-dialog [role="option"]');
    await expect(rows).toHaveCount(ARTIFACT_THREAD.artifacts.length);
    // Polled, not measured once: the dialog animates in (`zoom-in-95`, 200ms)
    // and a box read mid-flight is the row scaled by ~0.96, i.e. 42.5px — a
    // false alarm about a rule that is in fact loaded.
    const rowHeights = () =>
      rows.evaluateAll((elements) =>
        elements.map((element) => element.getBoundingClientRect().height),
      );
    await expect
      .poll(async () => Math.min(...(await rowHeights())))
      .toBeGreaterThanOrEqual(44);

    // Under 16px iOS zooms the whole page in when the search box takes focus.
    await expect
      .poll(() =>
        page
          .locator(".mobile-model-dialog input")
          .evaluate((element) =>
            parseFloat(getComputedStyle(element).fontSize),
          ),
      )
      .toBeGreaterThanOrEqual(16);
  });

  test("picking the file that is already open closes the list", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(artifactPath(HTML_PATH));

    const switcher = page.getByTestId("mobile-artifact-switcher");
    await expect(switcher).toBeVisible({ timeout: 15_000 });
    await switcher.click();

    const current = page.getByTestId("mobile-artifact-option-report.html");
    await expect(current).toBeVisible();
    await current.click();

    // The URL does not change, so nothing else would tear the dialog down:
    // cmdk reports the selection but never closes its parent dialog.
    await expect(current).toHaveCount(0);
    await expect(page.getByTestId("mobile-artifact-title")).toHaveText(
      "report.html",
    );
  });

  test("the title's file name opens the switcher and lands on another file", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(artifactPath(HTML_PATH));

    const switcher = page.getByTestId("mobile-artifact-switcher");
    await expect(switcher).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("mobile-artifact-title")).toHaveText(
      "report.html",
    );

    await switcher.click();
    await page.getByTestId("mobile-artifact-option-data.json").click();

    await expect(page.getByTestId("mobile-artifact-code")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("mobile-artifact-title")).toHaveText(
      "data.json",
    );
    expect(new URL(page.url()).pathname).not.toContain("/m/");
  });

  test("typing a keyword filters the file list", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [ARTIFACT_THREAD] });
    await mockArtifactBytes(page);
    await page.goto(artifactPath(HTML_PATH));

    await page.getByTestId("mobile-artifact-switcher").click({
      timeout: 15_000,
    });
    // All three are listed before anything is typed.
    await expect(
      page.getByTestId("mobile-artifact-option-chart.png"),
    ).toBeVisible();

    await page.getByPlaceholder("Filter files by name").fill("chart");

    await expect(
      page.getByTestId("mobile-artifact-option-chart.png"),
    ).toBeVisible();
    await expect(
      page.getByTestId("mobile-artifact-option-report.html"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("mobile-artifact-option-data.json"),
    ).toHaveCount(0);
  });
});
