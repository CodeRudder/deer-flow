import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * Process timeline (docs/plans/2026-11-03-process-timeline-design.md +
 * user-reviewed interaction refinements), behind NEXT_PUBLIC_PROCESS_TIMELINE
 * (build-time flag: specs must run against a build made with the flag on).
 *
 * Covers the interaction matrix on a settled multi-step thread:
 * 1/2/6 — every row (tool call, reasoning, artifact) toggles on row click;
 *   expanded row click collapses back; the whole row area is the hit zone.
 * 5 — expanded detail clamps long content to 10 lines with a
 *   "View more (N characters left)" toggle.
 * (4 — streaming tail preview — is covered by unit tests; see the streaming
 * note in the PR.)
 */

const THREAD_ID = "00000000-0000-0000-0000-000000005001";

const longReasoning = Array.from(
  { length: 14 },
  (_, i) => `thought-${i + 1}`,
).join("\n");

const THREAD_MESSAGES = [
  {
    type: "human",
    id: "h1",
    content: [{ type: "text", text: "research and draft" }],
  },
  {
    type: "ai",
    id: "a1",
    content: "",
    additional_kwargs: { reasoning_content: longReasoning },
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
    additional_kwargs: { reasoning_content: "wrap up: 3 files drafted" },
  },
];

test.describe("process timeline interactions", () => {
  // Historical threads must render through the same timeline path as live
  // sessions: this spec loads a settled checkpoint-style thread fixture (no
  // streaming) and asserts the step rows, never the legacy panel.
  test("historical thread renders the timeline; row click toggles; detail clamps to 10 lines with view-more; artifact row no longer jumps", async ({
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
    await expect(page.getByText("All done — here is the draft.")).toBeVisible({
      timeout: 15_000,
    });

    const rows = page.locator('[data-testid="process-step-row"]');
    // reasoning + web_search + narration + write_file + the answer's wrap-up
    // reasoning (compact step row, not an in-bubble block)
    await expect(rows).toHaveCount(5);

    // Collapsed preview shows the head lines of the long reasoning.
    const firstPreview = rows
      .first()
      .locator('[data-testid="process-step-preview"]');
    await expect(firstPreview).toContainText("thought-1");

    // 1/2/6 — clicking the first row (whole-row hit zone, via the preview
    // area inside the row button) expands the reasoning detail.
    await firstPreview.click();
    const detail = page.locator('[data-testid="process-step-detail"]').first();
    await expect(detail).toBeVisible();

    // 5 — the detail is clamped to 10 lines with a view-more toggle.
    await expect(detail).toContainText("thought-10");
    await expect(detail).not.toContainText("thought-11");
    await expect(
      page.locator('[data-testid="process-step-clamp-more"]'),
    ).toContainText(/View more \(\d+ characters? left\)/);
    await page.locator('[data-testid="process-step-clamp-more"]').click();
    await expect(detail).toContainText("thought-14");
    // clicking again re-collapses the block to the 10-line view
    await page.locator('[data-testid="process-step-clamp-less"]').click();
    await expect(detail).not.toContainText("thought-14");

    // 6 — clicking the expanded row collapses it back to the compact state.
    await page.locator('[data-testid="process-step-toggle"]').first().click();
    await expect(
      page.locator('[data-testid="process-step-detail"]'),
    ).toHaveCount(0);

    // 1 — the write_file row toggles instead of jumping to the artifact panel.
    const artifactRow = page
      .locator('[data-testid="process-step-row"][data-step-variant="toolCall"]')
      .nth(1); // web_search first, write_file second
    await artifactRow.locator('[data-testid="process-step-toggle"]').click();
    const artifactDetail = page
      .locator('[data-testid="process-step-detail"]')
      .first();
    await expect(artifactDetail).toBeVisible();
    // the artifact action now lives inside the expanded detail
    await expect(
      page.locator('[data-testid="process-step-open-artifact"]'),
    ).toContainText("draft.md");
    // detail-internal clicks never collapse the row
    await page.locator('[data-testid="process-step-open-artifact"]').click();
    await expect(artifactDetail).toBeVisible();

    // The answer's reasoning renders exactly once — as the compact step row,
    // never as the unbounded in-bubble Reasoning block.
    await expect(page.getByText("wrap up: 3 files drafted")).toHaveCount(1);
  });
});

// Mobile viewport regression (390×844-class): the mobile chat page reuses the
// desktop transcript; the same historical thread must render as timeline rows
// (never the legacy "ran N steps · M tools" collapsed card), and reasoning
// stays compact.
test.describe("process timeline on mobile", () => {
  // Runs under the config's mobile project (iPhone 13, 390×844-class); the
  // desktop project repeats it as a parity check.
  test("historical thread renders step rows at phone width", async ({
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
    await expect(page.getByText("All done — here is the draft.")).toBeVisible({
      timeout: 15_000,
    });

    const rows = page.locator('[data-testid="process-step-row"]');
    await expect(rows).toHaveCount(5);
    await expect(page.getByText("wrap up: 3 files drafted")).toHaveCount(1);
    await expect(
      page.locator('[data-testid="mobile-collapsed-steps"]'),
    ).toHaveCount(0);
    // expanded detail is reachable on tap
    await rows.first().locator('[data-testid="process-step-toggle"]').click();
    await expect(
      page.locator('[data-testid="process-step-detail"]').first(),
    ).toBeVisible();
  });
});
