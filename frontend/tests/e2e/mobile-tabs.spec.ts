import { devices, expect, test, type Page } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

/**
 * Mobile root-screen switching (T7, prototype ①⑥⑦, TEST_FLOW F8).
 *
 * Two properties are under test and they belong to different layers:
 *
 * 1. **Routing** — `/agents` and `/settings` exist only on the mobile tree.
 *    A phone must have them rewritten to `/m/agents` / `/m/settings`, and a
 *    desktop must not (there is no such desktop route; that asymmetry is
 *    accepted in plan §1.1.1). Read off the `x-middleware-rewrite` response
 *    header, which only a real Next server produces.
 * 2. **Navigation** — tapping a tab must land on the *public* path and the
 *    address bar must never show `/m/`. T1 hard-coded `/m/*` hrefs, so tapping
 *    a tab wrote `/m/workspace` into the URL; that regression is invisible to
 *    the rewrite assertions above (a client-side `router.push` of an already
 *    `/m/`-prefixed path is skipped by the middleware matcher, so it never
 *    shows up as a rewrite), which is why it is asserted here on the real
 *    address bar — including on the hop *out of* a thread, where the tab bar is
 *    deliberately absent and the back button is the only way out.
 *
 * `defaultBrowserType` is forced back to chromium because the iPhone presets
 * are WebKit-only and this suite installs chromium only (same as
 * `mobile-auth.spec.ts`).
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const IPHONE_UA = devices["iPhone 13"].userAgent;
const DESKTOP_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";

const THREADS = [
  {
    thread_id: MOCK_THREAD_ID,
    title: "Existing chat",
    updated_at: new Date().toISOString(),
  },
];

const CHAT_PATH = `/workspace/chats/${MOCK_THREAD_ID}`;

/**
 * The tab bar is the only `navigation` landmark on the mobile tree, so scoping
 * to it keeps the labels from colliding with the thread list's own heading.
 */
function tabBar(page: Page) {
  return page.getByRole("navigation");
}

function tab(page: Page, name: string) {
  return tabBar(page).getByRole("link", { name });
}

/** The address bar, which is what the `/m/` leak would corrupt. */
function addressBar(page: Page) {
  return page.url();
}

function pathnameOf(page: Page) {
  return new URL(page.url()).pathname;
}

/** Waits for a client-side navigation to settle on `path`. */
async function expectPathname(page: Page, path: string) {
  await expect.poll(() => pathnameOf(page)).toBe(path);
  // Every hop is checked, not just the final one: the whole point of F8-3 is
  // that `/m/` never appears at any step.
  expect(addressBar(page)).not.toContain("/m/");
}

// ---------------------------------------------------------------------------
// Middleware split for the two mobile-only roots
// ---------------------------------------------------------------------------

test.describe("Mobile root screen routing", () => {
  for (const path of ["/agents", "/settings"] as const) {
    test(`a mobile UA is rewritten from ${path} onto the mobile tree`, async ({
      request,
    }) => {
      const response = await request.get(path, {
        headers: { "user-agent": IPHONE_UA },
        maxRedirects: 0,
      });

      // The rewrite is the mechanism: the browser URL stays `/agents` while
      // the mobile screen renders it.
      expect(response.headers()["x-middleware-rewrite"]).toBe(`/m${path}`);
    });

    test(`a desktop UA does not get the mobile tree at ${path}`, async ({
      request,
    }) => {
      const response = await request.get(path, {
        headers: { "user-agent": DESKTOP_UA },
        maxRedirects: 0,
      });

      // `/agents` and `/settings` are mobile-only public paths (desktop agents
      // live at `/workspace/agents`, desktop settings is a dialog), so a
      // desktop UA gets a 404 rather than the mobile screen. The assertion
      // that matters is the absence of the rewrite.
      expect(response.headers()["x-middleware-rewrite"]).toBeUndefined();
    });
  }
});

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------

test.describe("Mobile tab navigation", () => {
  test("each tab opens its public root and never leaks /m/", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.goto("/workspace");
    await expect(page.getByTestId("mobile-thread-row")).toHaveCount(1, {
      timeout: 15_000,
    });
    await expectPathname(page, "/workspace");
    // The thread-list root is the mobile screen, not the desktop one.
    await expect(page.getByTestId("mobile-thread-search")).toBeVisible();

    await tab(page, "Agents").click();
    await expectPathname(page, "/agents");
    // The blank root renders its own title instead of a dead end.
    await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();
    await expect(tab(page, "Agents")).toHaveAttribute("aria-current", "page");

    await tab(page, "Settings").click();
    await expectPathname(page, "/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await expect(tab(page, "Settings")).toHaveAttribute("aria-current", "page");

    await tab(page, "Chats").click();
    await expectPathname(page, "/workspace");
    await expect(tab(page, "Chats")).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("mobile-thread-search")).toBeVisible();
  });

  test("a thread is full-bleed — the tab bar belongs to the roots only", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });
    await page.goto(CHAT_PATH);
    await expect(page.getByTestId("mobile-composer")).toBeVisible({
      timeout: 15_000,
    });
    await expectPathname(page, CHAT_PATH);

    // Prototype ② draws the composer straight onto the home indicator: the tab
    // bar is the three roots' (①⑥⑦), and a thread is a drill-down, so the two
    // would otherwise fight over the bottom edge. Structurally this is the
    // route living in `(app)/(fullbleed)` — not a pathname rule (T17).
    await expect(tabBar(page)).toHaveCount(0);

    // The hop out is still a client-side navigation, so it is where a `/m/`
    // leak would show up now that there is no tab to click.
    await page.getByTestId("mobile-chat-back").click();
    await expectPathname(page, "/workspace");
    await expect(tab(page, "Chats")).toHaveAttribute("aria-current", "page");
  });
});
