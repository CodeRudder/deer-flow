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
  await mockLangGraphAPI(page);
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
