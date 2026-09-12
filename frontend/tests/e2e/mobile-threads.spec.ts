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
 * One thread per time bucket, anchored to the start of the local calendar day.
 *
 * `threadTimeGroupId()` buckets by *calendar day*, so "now minus N hours" only
 * lands in yesterday when the wall clock happens to agree: run the suite at
 * 01:20 and `now - 26h` is two midnights back, the yesterday bucket stays empty
 * and the list renders two groups instead of three. Midnight-today is always
 * "today", one millisecond before it is always "yesterday", and neither can
 * drift into the future.
 */
const HOUR_MS = 60 * 60 * 1000;
const NOW = Date.now();
const START_OF_TODAY = new Date(
  new Date(NOW).getFullYear(),
  new Date(NOW).getMonth(),
  new Date(NOW).getDate(),
).getTime();

const THREADS: MockThread[] = [
  {
    thread_id: "11111111-1111-1111-1111-111111111111",
    title: "Today thread",
    updated_at: new Date(START_OF_TODAY).toISOString(),
  },
  {
    thread_id: "22222222-2222-2222-2222-222222222222",
    title: "Yesterday thread",
    updated_at: new Date(START_OF_TODAY - 1).toISOString(),
  },
  {
    thread_id: "33333333-3333-3333-3333-333333333333",
    title: "Earlier thread",
    updated_at: new Date(START_OF_TODAY - 10 * 24 * HOUR_MS).toISOString(),
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

/**
 * Prototype ①'s「● 生成中」row (T18).
 *
 * The state rides on the thread list response itself: the gateway's
 * `POST /api/threads/search` returns `ThreadResponse.status`, which is
 * `running` between run creation and run completion (`threads_meta`, written by
 * the gateway's run-submission path and reset by the run worker). The row
 * therefore needs no extra request — the fixture below carries the same field.
 */
test.describe("Mobile thread list generating state", () => {
  const RUNNING_ID = "44444444-4444-4444-4444-444444444444";
  const SETTLED_ID = "55555555-5555-5555-5555-555555555555";

  const LIST: MockThread[] = [
    {
      thread_id: RUNNING_ID,
      title: "Running thread",
      // Anchored, not `now - 1h`: see `START_OF_TODAY` above — before 02:00
      // local an hour back is already yesterday, which would move this row
      // into another group mid-suite.
      updated_at: new Date(START_OF_TODAY).toISOString(),
      status: "running",
    },
    {
      thread_id: SETTLED_ID,
      title: "Settled thread",
      // One millisecond earlier so the sort order stays fixed regardless of
      // what time the suite runs.
      updated_at: new Date(START_OF_TODAY - 1).toISOString(),
      status: "idle",
    },
  ];

  test("marks the row with a run in flight and leaves the settled row plain", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: LIST });
    await page.goto("/workspace");

    // The label sits on the summary line, right where the preview would be.
    const running = rowFor(page, "Running thread");
    await expect(running).toBeVisible({ timeout: 15_000 });
    await expect(running.getByTestId("mobile-thread-generating")).toContainText(
      "Generating",
    );
    // Prototype ① tints the whole row: `background: var(--accent)`.
    await expect(running).toHaveClass(/bg-accent/);

    // A finished thread must not be dressed up as a running one.
    const settled = rowFor(page, "Settled thread");
    await expect(settled).toBeVisible();
    await expect(settled.getByTestId("mobile-thread-generating")).toHaveCount(
      0,
    );
    await expect(settled).not.toHaveClass(/bg-accent/);
  });

  test("the marked row still navigates when tapped", async ({ page }) => {
    // The label is an extra element inside the row's touch target; it must not
    // swallow the tap.
    mockLangGraphAPI(page, { threads: LIST });
    await page.goto("/workspace");

    const running = rowFor(page, "Running thread");
    await expect(running).toBeVisible({ timeout: 15_000 });
    await running.locator("a").click();

    await expect(page).toHaveURL(`/workspace/chats/${RUNNING_ID}`);
  });
});

/**
 * A failed fetch is not an empty inbox.
 *
 * Reported from a real run: with the gateway down, `/workspace` rendered
 * "No conversations yet." — the list's empty state — so an outage read as
 * "my data is gone". The empty state was keyed on `length === 0` alone, which
 * is equally true before the first page arrives and after a request fails.
 *
 * Both branches are now distinct and this pins them. The failure is injected
 * *after* `mockLangGraphAPI` because Playwright resolves routes most-recently
 * registered first, so this handler wins for the one endpoint under test.
 */
test.describe("Mobile thread list failure", () => {
  test("a failed load reports failure instead of claiming there is no data", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.route("**/api/langgraph/threads/search", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "gateway is down" }),
      }),
    );

    await page.goto("/workspace");

    // Not immediate: the SDK retries a failed search for ~22s before rejecting,
    // and the list asks React Query for no retries of its own so that ~22s is
    // the whole cost. With the default policy this took 106s (measured), which
    // is long enough that the spinner reads as "no data" rather than "failed".
    await expect(page.getByTestId("mobile-thread-load-error")).toBeVisible({
      timeout: 45_000,
    });

    // The whole point: the empty state must never be what the user sees here.
    await expect(page.getByTestId("mobile-thread-empty")).toHaveCount(0);
    await expect(page.getByText("No conversations yet.")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  test("a genuinely empty account still shows the empty state", async ({
    page,
  }) => {
    // Guards the opposite mistake: the new error branch must not swallow the
    // real empty state.
    mockLangGraphAPI(page, { threads: [] });
    await page.goto("/workspace");

    await expect(page.getByTestId("mobile-thread-empty")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("mobile-thread-load-error")).toHaveCount(0);
  });
});
