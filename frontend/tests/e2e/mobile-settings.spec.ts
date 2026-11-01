import { devices, expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * Mobile settings: the grouped root and its sub-screens (T22, prototype ⑦,
 * `FEATURE_LIST.md` §1.4).
 *
 * Four properties, each of which a "looks right in a screenshot" port can get
 * wrong:
 *
 * 1. **Completeness** — all eight retained sections are rows in the
 *    prototype's four groups, and the removed MCP tools entry (S7) has no row.
 * 2. **Read-only is visible before the tap** — S5/S8 are badged on the row.
 *    Tapping through is deliberately not the test: the prototype's annotation
 *    says the badge exists so the constraint is known *before* the tap.
 * 3. **Drill-down without the `/m/` leak** — every hop is checked on the real
 *    address bar, not on the rewrite header: `<Link href="/settings/models">`
 *    is what a phone ends up showing, the middleware rewrites it server-side,
 *    and `/m/` must never surface (plan §§1.1.1, 1.2.1).
 * 4. **The sub-screens keep the tab bar** — they live in `(app)/(tabbed)`
 *    because they have no footer to fight it for the bottom edge, so the bar
 *    (and the `Settings` tab staying lit) must still be there on a drill-down.
 *
 * `defaultBrowserType` is forced back to chromium for the same reason as
 * `mobile-tabs.spec.ts`: the iPhone presets are WebKit-only and this suite
 * installs chromium only.
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const MEMORY = {
  version: "1.0",
  lastUpdated: "2026-09-01T00:00:00Z",
  user: {
    workContext: {
      summary: "Works on DeerFlow.",
      updatedAt: "2026-09-01T00:00:00Z",
    },
    personalContext: { summary: "", updatedAt: "" },
    topOfMind: { summary: "", updatedAt: "" },
  },
  history: {
    recentMonths: { summary: "", updatedAt: "" },
    earlierContext: { summary: "", updatedAt: "" },
    longTermBackground: { summary: "", updatedAt: "" },
  },
  facts: [
    {
      id: "fact-1",
      content: "Prefers concise answers.",
      category: "preference",
      confidence: 0.9,
      createdAt: "2026-08-01T00:00:00Z",
      source: "manual",
    },
  ],
};

const MANAGED_MODELS = {
  models: [
    {
      index: 0,
      name: "gpt-4o",
      model: "gpt-4o",
      display_name: "GPT-4o",
      use: "langchain_openai:ChatOpenAI",
    },
  ],
};

/**
 * The endpoints `mockLangGraphAPI` does not cover.
 *
 * Registered after it, so these win wherever they overlap — none of them does:
 * the shared mock leaves `/api/memory` and `/api/models/config` untouched.
 */
async function mockSettingsBackend(page: Page) {
  await page.route("**/api/memory", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MEMORY),
    }),
  );

  await page.route("**/api/models/config", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MANAGED_MODELS),
    }),
  );
}

const ROWS = [
  { testId: "mobile-settings-row-account", label: "Account" },
  { testId: "mobile-settings-row-appearance", label: "Appearance" },
  { testId: "mobile-settings-row-notification", label: "Notification" },
  { testId: "mobile-settings-row-memory", label: "Memory" },
  { testId: "mobile-settings-row-channels", label: "Channels" },
  { testId: "mobile-settings-row-models", label: "Models" },
  { testId: "mobile-settings-row-skills", label: "Skills" },
  { testId: "mobile-settings-row-about", label: "About" },
] as const;

/** The address bar, which is what the `/m/` leak would corrupt. */
function pathnameOf(page: Page) {
  return new URL(page.url()).pathname;
}

/** Waits for a client-side navigation to settle, checking the leak each hop. */
async function expectPathname(page: Page, path: string) {
  await expect.poll(() => pathnameOf(page)).toBe(path);
  expect(page.url()).not.toContain("/m/");
}

/** Opens a row by its label and lands on the sub-screen it points at. */
async function openRow(page: Page, testId: string, path: string) {
  await page.getByTestId(testId).click();
  await expectPathname(page, path);
}

test.describe("Mobile settings root", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockSettingsBackend(page);
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await expectPathname(page, "/settings");
  });

  test("shows every retained section in the prototype's groups, and no MCP entry", async ({
    page,
  }) => {
    for (const group of [
      "Personal",
      "Connections",
      "Models & capabilities",
      "Other",
    ]) {
      await expect(page.getByRole("heading", { name: group })).toBeVisible();
    }

    for (const row of ROWS) {
      await expect(page.getByTestId(row.testId)).toContainText(row.label);
    }

    // S7 was removed, not disabled: no row, no link, no "coming soon".
    await expect(page.getByRole("link", { name: /tools/i })).toHaveCount(0);
  });

  test("badges the read-only rows and only those", async ({ page }) => {
    await expect(page.getByText("Read only")).toHaveCount(2);
    await expect(page.getByTestId("mobile-settings-row-models")).toContainText(
      "Read only",
    );
    await expect(page.getByTestId("mobile-settings-row-skills")).toContainText(
      "Read only",
    );
    await expect(
      page.getByTestId("mobile-settings-row-channels"),
    ).not.toContainText("Read only");
  });

  test("every row clears the 44px touch floor", async ({ page }) => {
    for (const row of ROWS) {
      const box = await page.getByTestId(row.testId).boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
  });
});

test.describe("Mobile settings sub-screens", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockSettingsBackend(page);
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  });

  test("models opens read-only, on the public path, with the tab bar intact", async ({
    page,
  }) => {
    await openRow(page, "mobile-settings-row-models", "/settings/models");

    // The screen says read-only twice: the header pill and the notice.
    await expect(page.getByText("Read only").first()).toBeVisible();
    await expect(
      page.getByText(/add, edit and remove on the desktop/i),
    ).toBeVisible();
    await expect(page.getByText("GPT-4o")).toBeVisible();
    // None of the desktop's write controls came along.
    await expect(page.getByRole("button", { name: /add model/i })).toHaveCount(
      0,
    );
    await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);

    // The tab bar belongs to `(tabbed)`, so a drill-down keeps it — and the
    // Settings tab stays lit.
    await expect(page.getByRole("navigation")).toBeVisible();
    await expect(
      page.getByRole("navigation").getByRole("link", { name: "Settings" }),
    ).toHaveAttribute("aria-current", "page");

    // The way up is the header's back link, which is a public path.
    await page.getByTestId("mobile-settings-back").click();
    await expectPathname(page, "/settings");
  });

  test("skills opens read-only, showing the enabled state", async ({
    page,
  }) => {
    await openRow(page, "mobile-settings-row-skills", "/settings/skills");

    await expect(page.getByText("Read only").first()).toBeVisible();
    const toggle = page.getByRole("switch", { name: "data-analysis" });
    await expect(toggle).toBeVisible();
    // Read-only means the switch reports the state and cannot move.
    await expect(toggle).toBeDisabled();
    await expect(
      page.getByRole("button", { name: /create skill/i }),
    ).toHaveCount(0);
  });

  test("channels shows connection status without a binding flow", async ({
    page,
  }) => {
    await openRow(page, "mobile-settings-row-channels", "/settings/channels");

    // `mockLangGraphAPI` answers with a server that has channels switched off,
    // so the section reports that state rather than looking empty.
    await expect(
      page.getByText(/Channel connections are not enabled/i),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /connect/i })).toHaveCount(0);
  });

  test("appearance keeps the theme and language controls", async ({ page }) => {
    await openRow(
      page,
      "mobile-settings-row-appearance",
      "/settings/appearance",
    );

    await expect(page.getByRole("radio", { name: /system/i })).toBeVisible();
    await expect(page.getByRole("radio", { name: /light/i })).toBeVisible();
    await expect(page.getByRole("radio", { name: /dark/i })).toBeVisible();
    await expect(page.getByRole("radio", { name: "中文" })).toBeVisible();
  });

  test("memory reads the stored memory and stays writable", async ({
    page,
  }) => {
    await openRow(page, "mobile-settings-row-memory", "/settings/memory");

    await expect(page.getByText("Works on DeerFlow.")).toBeVisible();
    await expect(page.getByText("Prefers concise answers.")).toBeVisible();
    // S4 is the section that keeps its writes, so the controls are present.
    await expect(page.getByTestId("mobile-memory-add")).toBeVisible();
    await expect(page.getByTestId("mobile-memory-clear")).toBeVisible();
  });

  test("account opens the password form with the tab bar still below it", async ({
    page,
  }) => {
    await openRow(page, "mobile-settings-row-account", "/settings/account");

    await expect(page.getByLabel("Current password")).toBeVisible();
    await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible();
    await expect(page.getByRole("navigation")).toBeVisible();
  });

  test("about reports the build's version", async ({ page }) => {
    await openRow(page, "mobile-settings-row-about", "/settings/about");

    await expect(page.getByText("Version")).toBeVisible();
    await expect(page.getByTestId("mobile-about-version")).toHaveText(
      /^v\d+\.\d+\.\d+$/,
    );
  });
});
