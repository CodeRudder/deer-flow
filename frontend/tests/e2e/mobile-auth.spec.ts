import { devices, expect, test } from "@playwright/test";

/**
 * Mobile auth routing (T3).
 *
 * Scope note: this file asserts what only a real Next server can show — that the
 * middleware splits the auth URLs by User-Agent, and that both trees agree on the
 * redirect for an already-signed-in visitor. The *rendered* mobile auth pages
 * cannot be reached from this harness: `playwright.config.ts` starts the app with
 * `DEER_FLOW_AUTH_DISABLED=1`, and `getServerSideUser()` returns a synthetic
 * authenticated user for that flag without issuing a request, so the auth layout
 * redirects before anything paints and `page.route()` has no call to intercept.
 * Those assertions live in `tests/unit/app/m/mobile-auth-pages.test.tsx`.
 *
 * The presets still matter: `devices` supplies the iPhone User-Agent, which is
 * what the middleware branches on. `defaultBrowserType` is overridden back to
 * `chromium` because the iPhone presets are WebKit-only and this suite installs
 * chromium only.
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const IPHONE_UA = devices["iPhone 13"].userAgent;
const DESKTOP_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";

const AUTH_PATHS = ["/login", "/setup", "/auth/callback"] as const;

test.describe("Mobile auth routing", () => {
  for (const path of AUTH_PATHS) {
    test(`a mobile UA is rewritten from ${path} onto the mobile tree`, async ({
      request,
    }) => {
      const response = await request.get(path, {
        headers: { "user-agent": IPHONE_UA },
        maxRedirects: 0,
      });

      // The rewrite is the whole mechanism: the browser URL stays `/login`
      // while the mobile tree renders it.
      expect(response.headers()["x-middleware-rewrite"]).toBe(`/m${path}`);
    });

    test(`a desktop UA keeps the desktop tree at ${path}`, async ({
      request,
    }) => {
      const response = await request.get(path, {
        headers: { "user-agent": DESKTOP_UA },
        maxRedirects: 0,
      });

      // No rewrite — this is the safety property of the split. A redirect may
      // still be present; that is the pre-existing auth guard, not a rewrite.
      expect(response.headers()["x-middleware-rewrite"]).toBeUndefined();
    });
  }

  test("both trees redirect an already-authenticated visitor to the workspace", async ({
    request,
  }) => {
    // Guard parity. The E2E app runs with auth disabled, which reports a
    // synthetic authenticated user, so both layouts take their `authenticated`
    // arm — the mobile one must redirect exactly like the desktop one
    // (`src/app/(auth)/layout.tsx`) rather than rendering a sign-in form.
    for (const userAgent of [IPHONE_UA, DESKTOP_UA]) {
      const response = await request.get("/login", {
        headers: { "user-agent": userAgent },
        maxRedirects: 0,
      });

      expect(response.status()).toBe(307);
      expect(response.headers().location).toBe("/workspace");
    }
  });
});
