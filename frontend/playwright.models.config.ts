/**
 * Playwright config for the model-configuration E2E suite.
 *
 * Deliberately separate from `playwright.config.ts`:
 *
 *   - That config starts a local server with `DEER_FLOW_AUTH_DISABLED=1` and a
 *     mocked backend. This suite needs the opposite — a REAL deployment with
 *     auth enabled, because the thing under test is the config.yaml write
 *     protocol (surgical edit, comment preservation, backup, rollback), which
 *     a mocked backend would not exercise at all.
 *   - So there is no `webServer` here: the target must already be running.
 *
 * Target selection (defaults to a local `make dev`):
 *
 *   cd frontend && DEER_FLOW_E2E_PASSWORD='...' pnpm e2e:models
 *
 *   DEER_FLOW_E2E_BASE_URL=http://192.168.2.10:3000 \
 *   DEER_FLOW_E2E_EMAIL=admin@sz-jlc.com \
 *   DEER_FLOW_E2E_PASSWORD='...' pnpm e2e:models
 */

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "model-settings.spec.ts",

  // Serial: every test writes to the same config.yaml, and a concurrent write
  // would race the backup/restore assertions.
  fullyParallel: false,
  workers: 1,

  forbidOnly: !!process.env.CI,
  retries: 0, // a retry would re-run writes against shared state
  reporter: [["list"]],

  // Generous: each test does a real HTTP round trip that also rewrites a file
  // on a possibly-remote host.
  timeout: 60_000,

  use: {
    baseURL: process.env.DEER_FLOW_E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  // No webServer on purpose — see the header comment.
});
