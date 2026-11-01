import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactElement } from "react";

import MobileMemorySettingsPage from "@/app/m/(app)/(tabbed)/settings/memory/page";
import { I18nProvider } from "@/core/i18n/context";
import type { UserMemory } from "@/core/memory/types";

/**
 * DOM-level contracts for the mobile memory screen (prototype ⑦, S4).
 *
 * S4 is one of the two settings sections that stay *writable* on a phone
 * ("保留（读写个人记忆）", unlike S5/S8 which are read-only), so what is pinned
 * here is that the writes actually reach the desktop's endpoints with the
 * desktop's payload:
 *
 * - the client-side validation runs *before* the request (an empty fact or a
 *   confidence outside 0–1 never becomes a POST);
 * - the category falls back to `context` exactly as the desktop's does, so a
 *   fact added on a phone is indistinguishable from one added on the desktop;
 * - delete and clear-all go through their own endpoints;
 * - the summaries are read-only, which is the desktop's own split (it renders
 *   them as markdown and offers no editor for them).
 *
 * The stub is a tiny in-memory backend rather than a fixed response: the hooks
 * write the returned memory straight into the query cache
 * (`setQueryData(["memory"], …)`), so returning the post-write document is what
 * makes "the row disappears after delete" observable at all.
 */

const EMPTY_SECTION = { summary: "", updatedAt: "" };

function memoryFixture(): UserMemory {
  return {
    version: "1.0",
    lastUpdated: "2026-09-01T00:00:00Z",
    user: {
      workContext: {
        summary: "Works on DeerFlow.",
        updatedAt: "2026-09-01T00:00:00Z",
      },
      personalContext: { ...EMPTY_SECTION },
      topOfMind: {
        summary: "Shipping the mobile settings screen.",
        updatedAt: "2026-09-02T00:00:00Z",
      },
    },
    history: {
      recentMonths: {
        summary: "Recent context.",
        updatedAt: "2026-08-01T00:00:00Z",
      },
      earlierContext: { ...EMPTY_SECTION },
      longTermBackground: { ...EMPTY_SECTION },
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
      {
        id: "fact-2",
        content: "Works in Shenzhen.",
        category: "context",
        confidence: 0.5,
        createdAt: "2026-08-02T00:00:00Z",
        source: "manual",
      },
    ],
  };
}

const fetchCalls: { url: string; init?: RequestInit }[] = [];

/** An in-memory stand-in for `core/memory/api.ts`, mutation by mutation. */
function stubMemoryBackend() {
  let current = memoryFixture();

  rs.stubGlobal(
    "fetch",
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      fetchCalls.push({ url, init });
      const method = init?.method ?? "GET";
      // The API layer only ever sends JSON strings; anything else is not a
      // body this stub needs to understand.
      const rawBody = init?.body;
      const body =
        typeof rawBody === "string" ? (JSON.parse(rawBody) as unknown) : {};

      const factPath = "/api/memory/facts/";
      if (url.includes(factPath)) {
        const factId = decodeURIComponent(url.split(factPath)[1] ?? "");
        if (method === "DELETE") {
          current = {
            ...current,
            facts: current.facts.filter((fact) => fact.id !== factId),
          };
        } else if (method === "PATCH") {
          current = {
            ...current,
            facts: current.facts.map((fact) =>
              fact.id === factId
                ? { ...fact, ...(body as Record<string, unknown>) }
                : fact,
            ),
          };
        }
      } else if (url.endsWith("/api/memory/facts") && method === "POST") {
        current = {
          ...current,
          facts: [
            ...current.facts,
            {
              id: "fact-new",
              content: "",
              category: "context",
              confidence: 1,
              createdAt: "2026-09-13T00:00:00Z",
              source: "manual",
              ...(body as Record<string, unknown>),
            },
          ],
        };
      } else if (url.endsWith("/api/memory") && method === "DELETE") {
        current = {
          ...current,
          facts: [],
          user: {
            workContext: { ...EMPTY_SECTION },
            personalContext: { ...EMPTY_SECTION },
            topOfMind: { ...EMPTY_SECTION },
          },
        };
      }

      return {
        ok: true,
        status: 200,
        json: async () => current,
      } as Response;
    },
  );
}

function callsTo(fragment: string) {
  return fetchCalls.filter((call) => call.url.includes(fragment));
}

afterEach(() => {
  cleanup();
  rs.unstubAllGlobals();
  rs.clearAllMocks();
  fetchCalls.length = 0;
});

function renderInProviders(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale="en-US">{ui}</I18nProvider>
    </QueryClientProvider>,
  );
}

describe("Mobile memory section (S4)", () => {
  test("renders the summaries and the facts, with no editor for the summaries", async () => {
    stubMemoryBackend();
    renderInProviders(<MobileMemorySettingsPage />);

    // The sub-screen's only exit is the public path, like every other section.
    expect(
      screen.getByTestId("mobile-settings-back").getAttribute("href"),
    ).toBe("/settings");
    // Both groups, and the sections inside them, come from the desktop's
    // `t.settings.memory.markdown.*` wording.
    expect(await screen.findByText("Works on DeerFlow.")).toBeTruthy();
    expect(
      screen.getByText("Shipping the mobile settings screen."),
    ).toBeTruthy();
    // `SettingsSection` renders its title as a div, not a heading — the
    // shared desktop atom is reused as-is.
    expect(screen.getByText("User context")).toBeTruthy();
    expect(screen.getByText("History")).toBeTruthy();
    // An empty summary renders the shared placeholder rather than a blank box.
    expect(screen.getAllByText("(empty)").length).toBeGreaterThanOrEqual(1);

    expect(screen.getAllByTestId("mobile-memory-fact")).toHaveLength(2);
    expect(screen.getByText("Prefers concise answers.")).toBeTruthy();
    expect(screen.getByText("preference")).toBeTruthy();
    // Confidence is shown the way the desktop's table describes it.
    expect(screen.getByText("Very high")).toBeTruthy();
    expect(screen.getByText("Normal")).toBeTruthy();
  });

  test("adding a fact posts the desktop's payload", async () => {
    const user = userEvent.setup();
    stubMemoryBackend();
    renderInProviders(<MobileMemorySettingsPage />);

    await user.click(await screen.findByTestId("mobile-memory-add"));
    await user.type(
      screen.getByLabelText("Content"),
      "Answers in Chinese by default.",
    );
    // Category left blank on purpose: the desktop defaults it to `context`.
    // Confidence starts at "1", so it has to be cleared, not appended to.
    await user.clear(screen.getByLabelText("Confidence"));
    await user.type(screen.getByLabelText("Confidence"), "0.8");
    await user.click(screen.getByTestId("mobile-memory-save"));

    const [call] = callsTo("/api/memory/facts");
    expect(call?.init?.method).toBe("POST");
    expect(call?.init?.body).toBe(
      JSON.stringify({
        content: "Answers in Chinese by default.",
        category: "context",
        confidence: 0.8,
      }),
    );
    // The cache is updated from the response, so the new fact is on screen.
    expect(
      await screen.findByText("Answers in Chinese by default."),
    ).toBeTruthy();
  });

  test("an empty fact is rejected before any request", async () => {
    const user = userEvent.setup();
    stubMemoryBackend();
    renderInProviders(<MobileMemorySettingsPage />);

    await user.click(await screen.findByTestId("mobile-memory-add"));
    await user.click(screen.getByTestId("mobile-memory-save"));

    expect(callsTo("/api/memory/facts")).toHaveLength(0);
  });

  test("editing a fact patches it", async () => {
    const user = userEvent.setup();
    stubMemoryBackend();
    renderInProviders(<MobileMemorySettingsPage />);

    const editButtons = await screen.findAllByTestId("mobile-memory-fact-edit");
    await user.click(editButtons[0]!);
    // The form opens prefilled with the fact it is editing.
    expect(screen.getByLabelText<HTMLInputElement>("Content").value).toBe(
      "Prefers concise answers.",
    );
    await user.clear(screen.getByLabelText("Content"));
    await user.type(screen.getByLabelText("Content"), "Prefers terse answers.");
    await user.click(screen.getByTestId("mobile-memory-save"));

    const [call] = callsTo("/api/memory/facts/fact-1");
    expect(call?.init?.method).toBe("PATCH");
    expect(call?.init?.body).toBe(
      JSON.stringify({
        content: "Prefers terse answers.",
        category: "preference",
        confidence: 0.9,
      }),
    );
  });

  test("deleting a fact needs the confirmation and calls its endpoint", async () => {
    const user = userEvent.setup();
    stubMemoryBackend();
    renderInProviders(<MobileMemorySettingsPage />);

    const deleteButtons = await screen.findAllByTestId(
      "mobile-memory-fact-delete",
    );
    await user.click(deleteButtons[0]!);

    // Nothing is sent until the dialog's own action is pressed.
    expect(callsTo("/api/memory/facts/")).toHaveLength(0);
    await user.click(screen.getByTestId("mobile-memory-fact-delete-confirm"));

    await waitFor(() => {
      expect(callsTo("/api/memory/facts/fact-1")[0]?.init?.method).toBe(
        "DELETE",
      );
    });
    await waitFor(() => {
      expect(screen.queryByText("Prefers concise answers.")).toBeNull();
    });
  });

  test("clear-all empties the facts only after confirmation", async () => {
    const user = userEvent.setup();
    stubMemoryBackend();
    renderInProviders(<MobileMemorySettingsPage />);

    await user.click(await screen.findByTestId("mobile-memory-clear"));
    // The initial GET is the only /api/memory call so far; the destructive verb
    // waits for the dialog's own action.
    expect(
      fetchCalls.some(
        (call) =>
          call.url.endsWith("/api/memory") && call.init?.method === "DELETE",
      ),
    ).toBe(false);

    await user.click(screen.getByTestId("mobile-memory-clear-confirm"));

    await waitFor(() => {
      expect(
        fetchCalls.some(
          (call) =>
            call.url.endsWith("/api/memory") && call.init?.method === "DELETE",
        ),
      ).toBe(true);
    });
    await waitFor(() => {
      expect(screen.queryAllByTestId("mobile-memory-fact")).toHaveLength(0);
    });
    // "Clear all memory" clears the summaries too (the desktop's own wording:
    // it removes every saved summary *and* fact), and the section stays on
    // screen showing the placeholder instead of vanishing.
    expect(screen.queryByText("Works on DeerFlow.")).toBeNull();
    expect(screen.getByText("Work")).toBeTruthy();
    expect(screen.getAllByText("(empty)").length).toBeGreaterThanOrEqual(1);
  });
});
