import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MobileNewAgentPage from "@/app/m/(app)/(tabbed)/agents/new/page";
import MobileAgentsPage from "@/app/m/(app)/(tabbed)/agents/page";
import { modelOptionsWithCurrent } from "@/components/workspace/mobile/agents/agent-model-picker";
import { I18nProvider } from "@/core/i18n/context";

/**
 * DOM-level contracts for the mobile "create agent manually" screen (G3).
 *
 * Three things are pinned, and all three are the desktop form's rules:
 *
 * 1. the name is checked **client-side and against the backend** before the
 *    create request — an invalid slug and a taken name both become inline
 *    errors, and neither reaches `POST /api/agents`;
 * 2. a successful create posts exactly `{name, description, model, soul}`,
 *    with the shared `MODEL_DEFAULT_VALUE` sentinel flattened to `model: null`;
 * 3. the gallery is refreshed by the mutation's own invalidation, which is why
 *    the two screens are rendered together below — the list must show the new
 *    agent without either screen knowing about the other.
 */

const mockRouter = rs.hoisted(() => ({
  push: rs.fn(),
  replace: rs.fn(),
  prefetch: rs.fn(),
  back: rs.fn(),
}));

rs.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => "/agents/new",
  useParams: () => ({}),
}));

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

/** `JSON.parse` needs a string; a non-string body is a bug in the test. */
function parseBody(init?: RequestInit): Record<string, unknown> {
  return JSON.parse(
    typeof init?.body === "string" ? init.body : "null",
  ) as Record<string, unknown>;
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  } as Response;
}

const fetchCalls: { url: string; init?: RequestInit }[] = [];
/** The backend's agent table: `POST` appends, `GET /api/agents` reads it. */
let agentList: AgentFixture[] = [];
/** Answer of the name-availability pre-check. */
let nameAvailable = true;

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

      if (url.includes("/api/agents/check")) {
        return jsonResponse({ available: nameAvailable, name: "new-agent" });
      }
      if (url.endsWith("/api/agents")) {
        if (init?.method === "POST") {
          const created = parseBody(init) as unknown as AgentFixture;
          agentList = [...agentList, created];
          return jsonResponse(created);
        }
        return jsonResponse({ agents: agentList });
      }
      if (url.includes("/api/agents/")) {
        return jsonResponse({ detail: "not found" }, 404);
      }
      if (url.includes("/api/models")) {
        return jsonResponse({
          models: [
            {
              id: "m1",
              name: "gpt-5",
              model: "gpt-5",
              display_name: "GPT-5",
            },
          ],
          vision_models: [],
          token_usage: { enabled: false },
        });
      }
      return jsonResponse({}, 200);
    },
  );
}

function renderInProviders(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale="en-US">{ui}</I18nProvider>
    </QueryClientProvider>,
  );
}

/** The create requests the screen made, parsed. */
function createdBodies() {
  return fetchCalls
    .filter(
      (call) =>
        call.init?.method === "POST" && call.url.endsWith("/api/agents"),
    )
    .map((call) => parseBody(call.init));
}

afterEach(() => {
  cleanup();
  rs.unstubAllGlobals();
  rs.clearAllMocks();
  fetchCalls.length = 0;
  agentList = [];
  nameAvailable = true;
});

describe("modelOptionsWithCurrent", () => {
  test("keeps a pinned model that is no longer configured", () => {
    // Dropping it would silently rewrite the agent's model on the next save.
    expect(modelOptionsWithCurrent(["gpt-5"], "retired-model")).toEqual([
      "retired-model",
      "gpt-5",
    ]);
    // Already listed, or following the default: nothing to add.
    expect(modelOptionsWithCurrent(["gpt-5"], "gpt-5")).toEqual(["gpt-5"]);
    expect(modelOptionsWithCurrent(["gpt-5"], null)).toEqual(["gpt-5"]);
    // The sentinel is the "follow default" row, not a model name.
    expect(modelOptionsWithCurrent(["gpt-5"], "__default__")).toEqual([
      "gpt-5",
    ]);
  });
});

describe("Mobile agent create screen", () => {
  test("an invalid name is rejected before any request", async () => {
    stubBackend();
    renderInProviders(<MobileNewAgentPage />);

    await userEvent.type(screen.getByTestId("mobile-agent-name"), "has space");
    await userEvent.click(screen.getByTestId("mobile-agent-create-submit"));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Invalid name",
    );
    expect(createdBodies()).toHaveLength(0);
    // The availability pre-check is not reached either: the regex check runs
    // first (same order as the desktop form).
    expect(
      fetchCalls.filter((call) => call.url.includes("/check")),
    ).toHaveLength(0);
  });

  test("a taken name becomes an inline error instead of a failed create", async () => {
    nameAvailable = false;
    stubBackend();
    renderInProviders(<MobileNewAgentPage />);

    await userEvent.type(screen.getByTestId("mobile-agent-name"), "new-agent");
    await userEvent.click(screen.getByTestId("mobile-agent-create-submit"));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "already exists",
    );
    expect(createdBodies()).toHaveLength(0);
  });

  test("creating posts the desktop's body and refreshes the gallery", async () => {
    stubBackend();
    // Both screens on one query cache: the gallery must pick the new agent up
    // from `useCreateAgent`'s invalidation alone.
    renderInProviders(
      <>
        <MobileAgentsPage />
        <MobileNewAgentPage />
      </>,
    );

    // The gallery starts empty — nothing to look at yet.
    await screen.findByTestId("mobile-agents-empty");

    await userEvent.type(screen.getByTestId("mobile-agent-name"), "new-agent");
    await userEvent.type(
      screen.getByTestId("mobile-agent-description"),
      "Writes the weekly report.",
    );
    await userEvent.click(screen.getByTestId("mobile-agent-model-trigger"));
    await userEvent.click(
      await screen.findByTestId("mobile-agent-model-gpt-5"),
    );

    await userEvent.click(screen.getByTestId("mobile-agent-create-submit"));

    await waitFor(() => {
      expect(createdBodies()).toHaveLength(1);
    });
    expect(createdBodies()[0]).toEqual({
      name: "new-agent",
      description: "Writes the weekly report.",
      model: "gpt-5",
      soul: "",
    });

    // Back to the gallery — the public path, never `/m/`.
    expect(mockRouter.push).toHaveBeenCalledWith("/agents");

    // And the gallery says so: the invalidated `["agents"]` query refetched and
    // the new agent is on the list.
    await waitFor(() => {
      expect(screen.getByTestId("mobile-agent-card")).toBeTruthy();
    });
    expect(screen.queryByTestId("mobile-agents-empty")).toBeNull();
    expect(screen.getByText("new-agent")).toBeTruthy();
  });

  test("picking the default model sends model: null", async () => {
    stubBackend();
    renderInProviders(<MobileNewAgentPage />);

    await userEvent.type(screen.getByTestId("mobile-agent-name"), "new-agent");
    // Choose a real model first, then go back to the shared sentinel.
    await userEvent.click(screen.getByTestId("mobile-agent-model-trigger"));
    await userEvent.click(
      await screen.findByTestId("mobile-agent-model-gpt-5"),
    );
    await userEvent.click(screen.getByTestId("mobile-agent-model-trigger"));
    await userEvent.click(
      await screen.findByTestId("mobile-agent-model-default"),
    );

    await userEvent.click(screen.getByTestId("mobile-agent-create-submit"));

    await waitFor(() => {
      expect(createdBodies()).toHaveLength(1);
    });
    expect(createdBodies()[0]?.model).toBeNull();
    // `soul` stays an empty string: the desktop form sends it that way and the
    // backend treats "no soul yet" as an empty document.
    expect(createdBodies()[0]?.soul).toBe("");
  });
});
