import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactElement } from "react";

import MobileAgentsPage from "@/app/m/(app)/(tabbed)/agents/page";
import { I18nProvider } from "@/core/i18n/context";

/**
 * DOM-level contracts for the mobile agent gallery (prototype ⑥, G1).
 *
 * The screen is the desktop gallery's data in a different shape, so what is
 * pinned here is exactly the part that differs — and the part a phone can get
 * wrong silently:
 *
 * - the list is a **single column**, asserted on the container's own class
 *   contract because jsdom applies no CSS (same technique as
 *   `mobile-auth-pages.test.tsx`);
 * - loading, failure and empty stay three distinct states (`isLoading` alone
 *   would make a gateway outage look like an empty account);
 * - every card entry points at a **public** path — `/workspace/agents/...` for
 *   the chat, `/agents/<name>/edit` for the editor — so the middleware, not the
 *   component, decides which tree renders and `/m/` can never reach the
 *   address bar (plan §1.2.1);
 * - the `＋` opens the same two-option choice the desktop dialog shows, with
 *   the same two destinations.
 *
 * Strings come from the real `en-US` translations rather than a stub, so a
 * missing key fails here instead of rendering `undefined`.
 */

/** The router the screens push to; `push` is what the `/m/` assertions read. */
const mockRouter = rs.hoisted(() => ({
  push: rs.fn(),
  replace: rs.fn(),
  prefetch: rs.fn(),
  back: rs.fn(),
}));

rs.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => "/agents",
  useParams: () => ({}),
}));

// The API layer is the real one; only the two modules that would reach for
// env/network configuration at import time are stubbed, so the request bodies
// and URLs asserted below are the ones the app actually sends.
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
rs.mock("@/core/api/fetcher", () => ({
  fetch: (input: RequestInfo, init?: RequestInit) =>
    globalThis.fetch(input, init),
  getCsrfHeaders: () => ({}),
  readCsrfCookie: () => null,
  isStateChangingMethod: () => false,
}));

type AgentFixture = {
  name: string;
  description: string;
  model: string | null;
  tool_groups: string[] | null;
  skills: string[] | null;
};

const AGENTS: AgentFixture[] = [
  {
    name: "researcher",
    description: "Deep research with citations.",
    model: "gpt-5",
    tool_groups: ["web"],
    skills: ["data-analysis"],
  },
  {
    name: "weekly-report",
    description: "Writes the weekly report.",
    model: null,
    tool_groups: null,
    skills: null,
  },
];

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  } as Response;
}

/** Requests the screens made, so request-level assertions stay possible. */
const fetchCalls: { url: string; init?: RequestInit }[] = [];
let agentList: AgentFixture[] = [];
let listPending = false;
/** Set while a stubbed list request is deliberately left in flight. */
let pendingResolve: ((response: Response) => void) | null = null;

function stubBackend() {
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

      if (url.endsWith("/api/agents")) {
        if (listPending) {
          // Never settles: keeps the query in its loading state for the
          // assertion. Held in a variable so the executor is not an empty
          // function body.
          return new Promise<Response>((resolve) => {
            pendingResolve = resolve;
          });
        }
        return jsonResponse({ agents: agentList });
      }
      if (url.includes("/api/agents/")) {
        return jsonResponse({ detail: "not found" }, 404);
      }
      if (url.includes("/api/models")) {
        return jsonResponse({
          models: [],
          vision_models: [],
          token_usage: { enabled: false },
        });
      }
      return jsonResponse({}, 200);
    },
  );
}

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

afterEach(() => {
  cleanup();
  rs.unstubAllGlobals();
  rs.clearAllMocks();
  fetchCalls.length = 0;
  agentList = [];
  listPending = false;
  // Unblock the request left in flight by the loading-state test.
  pendingResolve?.(jsonResponse({ agents: [] }));
  pendingResolve = null;
});

describe("Mobile agent gallery", () => {
  test("renders one card per agent in a single column", async () => {
    agentList = AGENTS;
    stubBackend();
    renderInProviders(<MobileAgentsPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId("mobile-agent-card")).toHaveLength(2);
    });

    const list = screen.getByTestId("mobile-agent-list");
    // The class contract that produces the layout: a flex column, never a
    // grid — the desktop's `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3
    // xl:grid-cols-4` is what a phone must not inherit.
    expect(list.className).toContain("flex-col");
    expect(list.className).not.toContain("grid-cols");

    expect(screen.getByText("researcher")).toBeTruthy();
    expect(screen.getByText("Deep research with citations.")).toBeTruthy();
    expect(screen.getByText("weekly-report")).toBeTruthy();

    // A single column can still be wrong one screen tall: the entries are the
    // public paths, so the middleware lands a phone on the mobile chat tree
    // and the address bar never shows `/m/`.
    const chat = screen.getByTestId("mobile-agent-chat-researcher");
    expect(chat.getAttribute("href")).toBe(
      "/workspace/agents/researcher/chats/new",
    );
    expect(chat.getAttribute("href")).not.toContain("/m/");

    const edit = screen.getByTestId("mobile-agent-edit-researcher");
    expect(edit.getAttribute("href")).toBe("/agents/researcher/edit");

    // The whole card head is a second, larger tap target for the same chat.
    expect(
      screen.getByTestId("mobile-agent-open-researcher").getAttribute("href"),
    ).toBe("/workspace/agents/researcher/chats/new");
  });

  test("shows the loading state, not the empty state, while the list is in flight", () => {
    listPending = true;
    stubBackend();
    renderInProviders(<MobileAgentsPage />);

    expect(screen.getByTestId("mobile-agents-loading")).toBeTruthy();
    expect(screen.queryByTestId("mobile-agents-empty")).toBeNull();
    expect(screen.queryByTestId("mobile-agent-card")).toBeNull();
  });

  test("shows the empty state with a create entry point", async () => {
    agentList = [];
    stubBackend();
    renderInProviders(<MobileAgentsPage />);

    expect(await screen.findByTestId("mobile-agents-empty")).toBeTruthy();
    expect(screen.getByText("No custom agents yet")).toBeTruthy();
    expect(screen.queryByTestId("mobile-agents-loading")).toBeNull();

    // The empty state is a dead end without this: the header `＋` is 44px away
    // from the thumb's resting place, the button sits under the copy.
    await userEvent.click(screen.getByTestId("mobile-agents-empty-create"));
    expect(await screen.findByTestId("mobile-agent-create-sheet")).toBeTruthy();
  });

  test("a failed load reports failure instead of claiming there are no agents", async () => {
    rs.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      if (url.endsWith("/api/agents")) {
        return jsonResponse({ detail: "gateway is down" }, 500);
      }
      return jsonResponse({}, 200);
    });
    renderInProviders(<MobileAgentsPage />);

    expect(await screen.findByTestId("mobile-agents-load-error")).toBeTruthy();
    expect(screen.queryByTestId("mobile-agents-empty")).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  test("the new-agent sheet offers both create paths and routes to public paths", async () => {
    agentList = AGENTS;
    stubBackend();
    renderInProviders(<MobileAgentsPage />);

    await userEvent.click(await screen.findByTestId("mobile-new-agent"));

    const sheet = await screen.findByTestId("mobile-agent-create-sheet");
    expect(sheet.textContent).toContain("Create manually");
    expect(sheet.textContent).toContain("Create through chat");

    // The manual form is a screen of its own on a phone (SOUL.md needs the
    // height), both destinations are public. Picking an option closes the
    // sheet, so the second one is opened again first.
    await userEvent.click(
      screen.getByRole("button", { name: /create manually/i }),
    );
    expect(mockRouter.push).toHaveBeenCalledWith("/agents/new");

    mockRouter.push.mockClear();
    await userEvent.click(screen.getByTestId("mobile-new-agent"));
    await userEvent.click(
      await screen.findByRole("button", { name: /create through chat/i }),
    );
    expect(mockRouter.push).toHaveBeenCalledWith("/workspace/agents/new");

    for (const [target] of mockRouter.push.mock.calls) {
      expect(String(target).startsWith("/m/")).toBe(false);
    }
  });

  test("delete asks for confirmation before sending the request", async () => {
    agentList = AGENTS;
    stubBackend();
    renderInProviders(<MobileAgentsPage />);

    await userEvent.click(
      await screen.findByTestId("mobile-agent-delete-researcher"),
    );

    const confirm = await screen.findByTestId("mobile-agent-delete-confirm");
    // Nothing has been sent yet: the destructive action needs a second tap.
    expect(fetchCalls.some((call) => call.init?.method === "DELETE")).toBe(
      false,
    );

    await userEvent.click(confirm);
    await waitFor(() => {
      expect(fetchCalls.some((call) => call.init?.method === "DELETE")).toBe(
        true,
      );
    });
    expect(fetchCalls.find((call) => call.init?.method === "DELETE")?.url).toBe(
      "/api/agents/researcher",
    );
  });
});
