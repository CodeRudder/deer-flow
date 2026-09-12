import { defineConfig, devices } from "@playwright/test";

/**
 * Live mobile smoke suite — runs against a **real, already-running** DeerFlow
 * stack, not the mocked one.
 *
 * Why this exists: `playwright.config.ts` mocks every backend call with a
 * hand-written response shape and runs with `DEER_FLOW_AUTH_DISABLED=1`. That
 * makes it fast and hermetic, and it proves the pages *can* render — but it
 * cannot prove the pages talk to the real backend correctly. A wrong endpoint,
 * a response field the page reads under a different name, an auth or CSRF
 * regression, a `gateway_unavailable` state, or the middleware rewrite breaking
 * requests would all leave every mocked test green while the app shows a blank
 * page. That failure mode has no coverage without this suite.
 *
 * It has no `webServer` on purpose: start the stack first (`make dev` /
 * `make start`) and point this at it. It is deliberately NOT part of
 * `pnpm test:e2e` — it needs a live gateway, a real model, and credentials.
 *
 *   make dev                                              # or: make start
 *   DEER_FLOW_E2E_EMAIL=admin@sz-jlc.com \
 *   DEER_FLOW_E2E_PASSWORD=... pnpm test:live
 */
export default defineConfig({
  testDir: "./tests/live",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"]],
  timeout: 60_000,

  use: {
    baseURL: process.env.DEER_FLOW_E2E_BASE_URL ?? "http://localhost:2026",
    trace: "retain-on-failure",
  },

  // The suite branches on User-Agent itself, so it drives two projects from one
  // spec file rather than duplicating specs per device.
  projects: [{ name: "live-mobile", use: { ...devices["Desktop Chrome"] } }],
});
