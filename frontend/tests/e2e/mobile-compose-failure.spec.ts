import { devices, expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * T11 / F4-7 / G7 — a *failed* send must not eat the user's text.
 *
 * The backend turns every `/runs/stream` request into an HTTP 500, so the run is
 * guaranteed to fail. What makes this subtle (and what the original diagnosis in
 * `TEST_FLOW.md` got wrong) is that the composer is cleared the moment the send
 * is *submitted*, not when it succeeds:
 *
 * 1. `prompt-input.tsx` clears the composer as soon as the promise returned by
 *    `onSubmit` resolves;
 * 2. the LangGraph SDK resolves that promise as soon as the run is enqueued —
 *    `StreamManager.start` chains onto an internal queue and returns
 *    (`dist/ui/manager.js`), so the promise settles before the stream request is
 *    even sent;
 * 3. therefore a run that fails later can never reject it, and returning the
 *    promise (as opposed to returning `undefined`) does NOT keep the text.
 *
 * The fix is that the run's error state hands the submitted text back, so this
 * test asserts the end state: after the failure is reported, the composer holds
 * the text again. The immediate clear in between is the SDK's, not the page's,
 * and is asserted below so the behaviour is pinned either way.
 */

test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const CHAT_PATH = "/workspace/chats/new";

/**
 * One thinking-capable model. Without a model the composer never pins
 * `context.mode` and takes its deferred-submit branch, which is not the path a
 * real send takes once the model list has loaded.
 */
function mockModels(page: Page) {
  return page.route("**/api/models", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        models: [
          {
            id: "mock-model",
            name: "mock-model",
            model: "mock-model",
            display_name: "Mock Model",
            supports_thinking: true,
          },
        ],
        vision_models: [],
        token_usage: { enabled: false },
      }),
    }),
  );
}

function textarea(page: Page) {
  return page.getByTestId("mobile-composer").locator("textarea");
}

test.describe("Mobile composer — failed send", () => {
  test("hands the typed text back after the run fails", async ({ page }) => {
    // The SDK retries the 500 five times before reporting it — measured at
    // ~23s — which is far past the suite's 30s default.
    test.setTimeout(90_000);

    mockLangGraphAPI(page, { threads: [] });
    await mockModels(page);

    let streamRequests = 0;
    void page.route("**/runs/stream", (route) => {
      streamRequests += 1;
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "mock failure" }),
      });
    });

    await page.goto(CHAT_PATH);
    await expect(textarea(page)).toBeVisible({ timeout: 15_000 });

    await textarea(page).fill("Hello");
    await page.getByTestId("mobile-composer-send").click();

    // The send has to actually be attempted, otherwise everything below would
    // pass vacuously.
    await expect
      .poll(() => streamRequests, { timeout: 10_000 })
      .toBeGreaterThan(0);

    // Status quo, unchanged by the fix: submitting clears the composer at once
    // because the SDK reports "enqueued" as success.
    await expect(textarea(page)).toHaveValue("", { timeout: 10_000 });

    // The failure is only reported once the SDK's retry budget runs out; at that
    // point both the error and the returned text must be there.
    await expect(page.locator("[data-sonner-toast]").first()).toBeVisible({
      timeout: 60_000,
    });
    await expect(textarea(page)).toHaveValue("Hello", { timeout: 5_000 });
  });
});
