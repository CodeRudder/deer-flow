import { devices, expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI, type MockThread } from "./utils/mock-api";

/**
 * Mobile thread list (T4, prototype ①).
 *
 * The list is reachable without a real session: the E2E app runs with
 * `DEER_FLOW_AUTH_DISABLED=1`, so the mobile workspace layout takes its
 * `authenticated` arm and the page renders. `mockLangGraphAPI` answers
 * `/api/langgraph/threads/search` from the fixtures below, which is the only
 * endpoint this screen needs to paint.
 *
 * The presets matter for the routing assertions: `devices["iPhone 13"]`
 * supplies the iPhone User-Agent the middleware branches on. `defaultBrowserType`
 * is overridden back to `chromium` because the iPhone presets are WebKit-only
 * and this suite installs chromium only (same as `mobile-auth.spec.ts`).
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const IPHONE_UA = devices["iPhone 13"].userAgent;
const DESKTOP_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";

/**
 * One thread per time bucket. The timestamps are relative to "now" so the
 * buckets are stable whenever the suite runs: an hour ago is today, `now - 26h`
 * is yesterday from any local time (26h back crosses at most one midnight from
 * an hour ago), and `now - 10 days` is always earlier.
 */
const HOUR_MS = 60 * 60 * 1000;
const NOW = Date.now();

const THREADS: MockThread[] = [
  {
    thread_id: "11111111-1111-1111-1111-111111111111",
    title: "Today thread",
    updated_at: new Date(NOW - HOUR_MS).toISOString(),
  },
  {
    thread_id: "22222222-2222-2222-2222-222222222222",
    title: "Yesterday thread",
    updated_at: new Date(NOW - 26 * HOUR_MS).toISOString(),
  },
  {
    thread_id: "33333333-3333-3333-3333-333333333333",
    title: "Earlier thread",
    updated_at: new Date(NOW - 10 * 24 * HOUR_MS).toISOString(),
  },
];

const TODAY_ID = THREADS[0]!.thread_id;

/** The always-visible trigger, used for the action flows. */
function rowFor(page: Page, title: string) {
  return page.getByTestId("mobile-thread-row").filter({ hasText: title });
}

test.describe("Mobile thread routing", () => {
  test("a mobile UA is rewritten onto the mobile thread list", async ({
    request,
  }) => {
    const response = await request.get("/workspace", {
      headers: { "user-agent": IPHONE_UA },
      maxRedirects: 0,
    });

    // The rewrite is the mechanism: the browser URL stays `/workspace` while
    // `/m/workspace` renders.
    expect(response.headers()["x-middleware-rewrite"]).toBe("/m/workspace");
  });

  test("a desktop UA does not get the mobile thread list", async ({
    request,
  }) => {
    const response = await request.get("/workspace", {
      headers: { "user-agent": DESKTOP_UA },
      maxRedirects: 0,
    });

    // No rewrite. The 307 to `/workspace/chats/new` is the pre-existing desktop
    // entry redirect, not a mobile rewrite.
    expect(response.headers()["x-middleware-rewrite"]).toBeUndefined();
  });
});

test.describe("Mobile thread list", () => {
  test("rows render grouped by time", async ({ page }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.goto("/workspace");

    await expect(page.getByTestId("mobile-thread-row")).toHaveCount(3, {
      timeout: 15_000,
    });

    // Prototype order ①: 今天 / 昨天 / 更早, each heading above the row it
    // introduces.
    const groups = page.getByTestId("mobile-thread-group");
    await expect(groups).toHaveCount(3);
    await expect(groups.nth(0).getByRole("heading")).toHaveText("Today");
    await expect(groups.nth(1).getByRole("heading")).toHaveText("Yesterday");
    await expect(groups.nth(2).getByRole("heading")).toHaveText("Earlier");

    await expect(groups.nth(0).getByText("Today thread")).toBeVisible();
    await expect(groups.nth(1).getByText("Yesterday thread")).toBeVisible();
    await expect(groups.nth(2).getByText("Earlier thread")).toBeVisible();
  });

  test("long-press opens the action sheet with pin, rename and delete", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.goto("/workspace");

    const row = rowFor(page, "Today thread");
    await expect(row).toBeVisible({ timeout: 15_000 });

    // Hold past the 500ms threshold the component documents, then release.
    const box = await row.locator("a").boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await page.mouse.down();
    await page.waitForTimeout(700);
    await page.mouse.up();

    const sheet = page.getByTestId("mobile-thread-actions-sheet");
    await expect(sheet).toBeVisible();
    await expect(page.getByTestId("mobile-thread-action-pin")).toBeVisible();
    await expect(page.getByTestId("mobile-thread-action-rename")).toBeVisible();
    await expect(page.getByTestId("mobile-thread-action-delete")).toBeVisible();

    // The long-press must not also fire the row's navigation.
    expect(new URL(page.url()).pathname).toBe("/workspace");
  });

  test("tapping a row navigates to the public chat path, without /m/", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.goto("/workspace");

    const row = rowFor(page, "Today thread");
    await expect(row).toBeVisible({ timeout: 15_000 });
    // Also guards the long-press wiring: it must not swallow ordinary taps.
    await row.locator("a").click();

    await expect(page).toHaveURL(`/workspace/chats/${TODAY_ID}`);
    // The internal prefix must never leak into the address bar.
    expect(page.url()).not.toContain("/m/");
  });

  test("pin moves a row into a leading Pinned group, unpin removes it", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.goto("/workspace");

    const earlierRow = rowFor(page, "Earlier thread");
    await expect(earlierRow).toBeVisible({ timeout: 15_000 });
    await earlierRow.getByTestId("mobile-thread-row-more").click();
    await page.getByTestId("mobile-thread-action-pin").click();

    // A pinned thread leads the list regardless of its age — the whole point of
    // a pin, and the reason it is a group rather than an in-place hoist.
    const groups = page.getByTestId("mobile-thread-group");
    await expect(groups.nth(0).getByRole("heading")).toHaveText("Pinned");
    await expect(groups.nth(0).getByText("Earlier thread")).toBeVisible();
    await expect(page.getByTestId("mobile-thread-row").nth(0)).toContainText(
      "Earlier thread",
    );

    // The action now reads "Unpin" and restores the time groups.
    await rowFor(page, "Earlier thread")
      .getByTestId("mobile-thread-row-more")
      .click();
    await expect(page.getByTestId("mobile-thread-action-pin")).toHaveText(
      "Unpin",
    );
    await page.getByTestId("mobile-thread-action-pin").click();

    await expect(groups.nth(0).getByRole("heading")).toHaveText("Today");
    await expect(groups).toHaveCount(3);
  });

  test("rename opens a text input seeded with the current title", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.goto("/workspace");

    await rowFor(page, "Today thread")
      .getByTestId("mobile-thread-row-more")
      .click();
    await page.getByTestId("mobile-thread-action-rename").click();

    const input = page.getByLabel("Rename");
    await expect(input).toBeVisible();
    await expect(input).toHaveValue("Today thread");
  });

  test("delete asks for confirmation before removing the row", async ({
    page,
  }) => {
    let deletes = 0;
    mockLangGraphAPI(page, { threads: THREADS });
    page.on("request", (request) => {
      if (request.method() === "DELETE") deletes += 1;
    });
    await page.goto("/workspace");

    await rowFor(page, "Today thread")
      .getByTestId("mobile-thread-row-more")
      .click();
    await page.getByTestId("mobile-thread-action-delete").click();

    // Nothing is deleted until the destructive action is confirmed.
    const confirm = page.getByTestId("mobile-thread-delete-confirm");
    await expect(confirm).toBeVisible();
    expect(deletes).toBe(0);

    await confirm.click();
    await expect.poll(() => deletes).toBeGreaterThan(0);
  });

  test("search filters the list", async ({ page }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.goto("/workspace");

    await expect(page.getByTestId("mobile-thread-row")).toHaveCount(3, {
      timeout: 15_000,
    });

    await page.getByTestId("mobile-thread-search").fill("yesterday");

    await expect(page.getByTestId("mobile-thread-row")).toHaveCount(1);
    await expect(page.getByText("Yesterday thread")).toBeVisible();
    await expect(page.getByText("Today thread")).toHaveCount(0);

    // A query matching nothing shows the empty-search copy, not a blank screen.
    await page.getByTestId("mobile-thread-search").fill("zzz-no-match");
    await expect(page.getByTestId("mobile-thread-row")).toHaveCount(0);
    await expect(
      page.getByText("No conversations match your search."),
    ).toBeVisible();
  });

  test("the new-chat entry points at the public new-chat path", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.goto("/workspace");

    const newChat = page.getByTestId("mobile-new-chat");
    await expect(newChat).toBeVisible({ timeout: 15_000 });
    await expect(newChat).toHaveAttribute("href", "/workspace/chats/new");
  });

  test("the tab bar shows with the chats tab active", async ({ page }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.goto("/workspace");

    const tabBar = page.getByRole("navigation").filter({ hasText: "Chats" });
    await expect(tabBar).toBeVisible({ timeout: 15_000 });
    await expect(tabBar.getByRole("link", { name: "Chats" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
