import { devices, expect, test, type Page } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

/**
 * Mobile workspace file browser (390×844).
 *
 * The mobile chat page renders its own header (`MobileChatHeader`) — the
 * desktop header's triggers never appear on a phone — so the browser entry
 * point must live there. This suite pins:
 *
 * 1. the folder button is visible without any artifacts on the thread;
 * 2. tapping it opens the overlay drawer with the API's directory listing;
 * 3. tapping a directory navigates into it (the API receives the child path);
 * 4. the drawer can be closed via its close button / backdrop;
 * 5. a 390×844 screenshot of the open drawer for eyeball verification.
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const CHAT_PATH = `/workspace/chats/${MOCK_THREAD_ID}`;

/** True when the viewport is desktop-sized (desktop tree, shared panel). */
function viewportIsDesktop(page: Page) {
  const size = page.viewportSize();
  return !!size && size.width >= 768;
}

const ROOT_LISTING = {
  path: "/mnt/user-data",
  truncated: false,
  entries: [
    {
      name: "outputs",
      path: "/mnt/user-data/outputs",
      type: "directory",
      size: null,
      modified_at: "2026-11-01T12:00:00Z",
    },
    {
      name: "notes.txt",
      path: "/mnt/user-data/notes.txt",
      type: "file",
      size: 64,
      modified_at: "2026-11-02T08:00:00Z",
    },
  ],
};

const OUTPUTS_LISTING = {
  path: "/mnt/user-data/outputs",
  truncated: false,
  entries: [
    {
      name: "report.pdf",
      path: "/mnt/user-data/outputs/report.pdf",
      type: "file",
      size: 4096,
      modified_at: "2026-11-02T09:00:00Z",
    },
  ],
};

function mockWorkspaceFiles(page: Page) {
  return page.route(/\/api\/threads\/[^/]+\/workspace\/files/, (route) => {
    const url = new URL(route.request().url());
    const path = url.searchParams.get("path") ?? "/mnt/user-data";
    if (path === "/mnt/user-data/outputs") {
      return route.fulfill({ json: OUTPUTS_LISTING });
    }
    return route.fulfill({ json: ROOT_LISTING });
  });
}

async function openMobileChat(page: Page) {
  mockLangGraphAPI(page);
  await mockWorkspaceFiles(page);
  await page.goto(CHAT_PATH);
  await expect(page.getByTestId("mobile-chat-title")).toBeVisible({
    timeout: 15_000,
  });
}

test("workspace files entry opens the drawer on mobile", async ({ page }) => {
  await openMobileChat(page);

  // The entry point is visible on a 390px viewport with no artifacts.
  const trigger = page.getByTestId("mobile-chat-workspace-files");
  await expect(trigger).toBeVisible();

  // Closed by default: no drawer in the DOM.
  await expect(page.getByTestId("workspace-files-panel")).toHaveCount(0);

  await trigger.click();
  const panel = page.getByTestId("workspace-files-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByText("outputs")).toBeVisible();
  await expect(panel.getByText("notes.txt")).toBeVisible();

  // Directory navigation hits the API with the child path.
  await panel.getByText("outputs").click();
  await expect(panel.getByText("report.pdf")).toBeVisible();

  // Close via the drawer's close button.
  await panel.getByTestId("workspace-files-close").click();
  await expect(page.getByTestId("workspace-files-panel")).toHaveCount(0);

  // Reopen and close via the backdrop.
  await trigger.click();
  await expect(page.getByTestId("workspace-files-panel")).toBeVisible();
  await page.getByTestId("workspace-files-backdrop").click({
    position: { x: 10, y: 300 },
  });
  await expect(page.getByTestId("workspace-files-panel")).toHaveCount(0);
});

test("captures a 390x844 screenshot of the open drawer", async ({ page }) => {
  await openMobileChat(page);
  await page.getByTestId("mobile-chat-workspace-files").click();
  const panel = page.getByTestId("workspace-files-panel");
  await expect(panel.getByText("notes.txt")).toBeVisible();
  await page.screenshot({
    path: "tests/e2e/__screens__/mobile-workspace-files-drawer.png",
    fullPage: false,
  });
});

/**
 * Long-document preview must scroll (user-reported regression: markdown
 * taller than the viewport was clipped by the preview frame's
 * `overflow-hidden` because nothing inside was the scroll container).
 *
 * Runs against both the mobile drawer and the desktop shared panel: the
 * preview overlay is the same component, but the two mount points exercise
 * different outer height constraints.
 */
const LONG_MD = Array.from(
  { length: 120 },
  (_, i) => `\n\n## Section ${i + 1}\n\nParagraph body for section ${i + 1}.`,
).join("");

async function mockLongMarkdown(page: Page) {
  await page.route(/\/api\/threads\/[^/]+\/workspace\/files/, (route) => {
    const url = new URL(route.request().url());
    const path = url.searchParams.get("path") ?? "/mnt/user-data";
    if (path === "/mnt/user-data/outputs") {
      return route.fulfill({
        json: {
          path,
          truncated: false,
          entries: [
            {
              name: "long.md",
              path: "/mnt/user-data/outputs/long.md",
              type: "file",
              size: 48_000,
              modified_at: "2026-11-02T09:00:00Z",
            },
          ],
        },
      });
    }
    return route.fulfill({ json: ROOT_LISTING });
  });
  await page.route(/\/api\/threads\/[^/]+\/artifacts\/.*long\.md/, (route) =>
    route.fulfill({ contentType: "text/markdown", body: LONG_MD }),
  );
}

async function previewLongMarkdownAndScrollToBottom(page: Page) {
  const desktop = viewportIsDesktop(page);
  mockLangGraphAPI(page);
  await mockLongMarkdown(page);
  await page.goto(CHAT_PATH);
  if (desktop) {
    await expect(page.getByRole("button", { name: "Artifacts" })).toBeVisible({
      timeout: 15_000,
    });
  } else {
    await expect(page.getByTestId("mobile-chat-title")).toBeVisible({
      timeout: 15_000,
    });
  }

  // The entry point differs per tree: the mobile header's folder button vs.
  // the desktop header's artifacts trigger + panel tab.
  if (desktop) {
    // Hydration guard: on the lightweight zero-artifact page the button can
    // render server-side before React attaches handlers, so the first click
    // is lost. Keep clicking until the panel is actually mounted (the
    // resizable panel then takes its 60/40 width).
    await expect(async () => {
      await page.getByRole("button", { name: "Artifacts" }).click();
      await page.waitForTimeout(400);
      const width = await page.evaluate(
        () =>
          document.getElementById("artifacts")?.getBoundingClientRect().width ??
          0,
      );
      expect(width).toBeGreaterThan(200);
    }).toPass({ timeout: 30_000 });
    await page.getByTestId("workspace-panel-tab-files").click();
  } else {
    await page.getByTestId("mobile-chat-workspace-files").click();
  }
  const panel = page.getByTestId("workspace-files-panel");
  await expect(panel.getByText("outputs")).toBeVisible();
  await panel.getByText("outputs").click();
  await panel.getByText("long.md").click();

  const preview = page.getByTestId("workspace-files-preview");
  await expect(preview).toBeVisible();
  await expect(preview.getByText("Section 1", { exact: true })).toBeVisible();

  // The body must be an actual scroll container holding the whole document.
  const scrollable = await preview
    .getByTestId("workspace-files-preview-body")
    .evaluate((element) => {
      element.scrollTop = element.scrollHeight; // jump to the bottom
      return {
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight,
      };
    });
  expect(scrollable.scrollHeight).toBeGreaterThan(scrollable.clientHeight);
  // The tail of the document is now in view.
  await expect(
    preview.getByRole("heading", { name: "Section 120" }),
  ).toBeVisible();
}

test.describe("mobile drawer", () => {
  // Inherits the file-level iPhone 13 preset.
  test("long markdown preview scrolls to the bottom (390x844)", async ({
    page,
  }) => {
    await previewLongMarkdownAndScrollToBottom(page);
  });
});

test.describe("desktop shared panel", () => {
  // `defaultBrowserType` can't live in a describe group; overriding the
  // User-Agent (the middleware's branch key) plus the viewport is enough.
  test.use({
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport: { width: 1440, height: 900 },
  });

  test("long markdown preview scrolls to the bottom (1440x900)", async ({
    page,
  }) => {
    await previewLongMarkdownAndScrollToBottom(page);
  });
});
