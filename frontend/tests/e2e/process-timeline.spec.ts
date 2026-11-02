import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * Process timeline MVP (docs/plans/2026-11-03-process-timeline-design.md),
 * behind NEXT_PUBLIC_PROCESS_TIMELINE (build-time flag: the spec must run
 * against a build made with the flag on).
 *
 * These specs load a settled multi-step thread fixture — reasoning + tool
 * rounds + narration + answer + present-files — and assert the transcript
 * renders as single-line step rows instead of the legacy ChainOfThought panel.
 * Streaming-specific transitions (running ▶, #4304 answer→step degrade) are
 * covered by the normalizer/row unit tests.
 */

const THREAD_ID = "00000000-0000-0000-0000-000000005001";

const THREAD_MESSAGES = [
  {
    type: "human",
    id: "h0",
    content: [{ type: "text", text: "earlier question" }],
  },
  {
    type: "ai",
    id: "a0",
    content: [{ type: "text", text: "earlier answer" }],
  },
  {
    type: "human",
    id: "h1",
    content: [{ type: "text", text: "research and draft" }],
  },
  {
    type: "ai",
    id: "a1",
    content: "",
    additional_kwargs: { reasoning_content: "searching first" },
    tool_calls: [
      { id: "tc1", name: "web_search", args: { query: "deer flow" } },
    ],
  },
  {
    type: "tool",
    id: "t1",
    name: "web_search",
    tool_call_id: "tc1",
    content: JSON.stringify([{ url: "https://example.com", title: "Example" }]),
  },
  {
    type: "ai",
    id: "a2",
    content: "mid-run status update",
    additional_kwargs: { reasoning_content: "now drafting" },
    tool_calls: [
      {
        id: "tc2",
        name: "write_file",
        args: { path: "draft.md", content: "x" },
      },
    ],
  },
  {
    type: "tool",
    id: "t2",
    name: "write_file",
    tool_call_id: "tc2",
    content: '"ok"',
  },
  {
    type: "ai",
    id: "a3",
    content: [{ type: "text", text: "All done — here is the draft." }],
  },
  {
    type: "ai",
    id: "a4",
    content: "",
    tool_calls: [
      { id: "tc3", name: "present_files", args: { filepaths: ["draft.md"] } },
    ],
  },
];

test.describe("process timeline", () => {
  test("renders a multi-step run as single-line step rows", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: THREAD_ID,
          title: "Timeline thread",
          messages: THREAD_MESSAGES,
        },
      ],
    });

    await page.goto(`/workspace/chats/${THREAD_ID}`);
    await expect(
      page.getByText("All done — here is the draft."),
    ).toBeVisible({ timeout: 15_000 });

    // Four step rows: reasoning + web_search + reasoning + narration + write.
    const rows = page.locator('[data-testid="process-step-row"]');
    await expect(rows).toHaveCount(5);
    await expect(
      page.locator('[data-testid="process-step-row"][data-step-status="done"]'),
    ).toHaveCount(5);
    await expect(
      page.locator('[data-testid="process-step-row"][data-step-variant="narration"]'),
    ).toHaveCount(1);
    await expect(rows.first()).toContainText("searching first");
    await expect(page.locator('[data-testid="process-step-row"]').nth(1)).toContainText(
      "deer flow",
    );

    // The answer bubble and the present-files artifact entry render at their
    // timeline positions (after the steps).
    await expect(
      page.locator('[data-testid="process-step-detail"]'),
    ).toHaveCount(0);

    // Step rows expand on click into the left-ruled detail region.
    // The hover toolbar of the "earlier answer" bubble absolutely overlaps the
    // first row; use a row further down the timeline.
    await page
      .locator('[data-testid="process-step-toggle"]')
      .nth(2)
      .click();
    await expect(
      page.locator('[data-testid="process-step-detail"]').first(),
    ).toBeVisible();

    // No legacy big-card panel under the timeline.
    await expect(
      page.locator('[data-testid="mobile-collapsed-steps"]'),
    ).toHaveCount(0);
  });
});
