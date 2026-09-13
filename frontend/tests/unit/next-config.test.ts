import { describe, expect, it } from "@rstest/core";

/**
 * `compress` must stay off: it is what makes the proxied LangGraph SSE
 * (`…/runs/{id}/stream`) arrive incrementally instead of being buffered inside
 * a gzip stream. With compression on, a page reloaded mid-run never receives
 * the replayed reasoning/tool events until the run ends — see the comment on
 * the flag in `next.config.js`.
 */
describe("next.config", () => {
  it("keeps response compression disabled so SSE is not buffered", async () => {
    process.env.SKIP_ENV_VALIDATION = "1";
    const config = (await import("../../next.config.js")).default;

    expect(config.compress).toBe(false);
  });
});
