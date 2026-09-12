import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MobileEditAgentPage from "@/app/m/(app)/(tabbed)/agents/[agent_name]/edit/page";
import { whitelistState } from "@/components/workspace/mobile/agents/whitelist-row";
import { I18nProvider } from "@/core/i18n/context";

/**
 * DOM-level contracts for the mobile agent editor (G5).
 *
 * The screen exists to edit three fields and to *show* three others, and the
 * read-only half is the part that quietly breaks: `skills` / `tool_groups` are
 * three-valued (null = inherit all, `[]` = none, list = whitelist) and the
 * gallery card cannot tell `null` from `[]`. The acceptance criterion is that
 * the editor spells all three out, so each state is rendered here.
 *
 * The other half is the request: only SOUL.md, description and model may be
 * sent, because the backend merges by `model_fields_set` — a stray
 * `tool_groups` key from the read-only section would rewrite the whitelist.
 */

const mockRouter = rs.hoisted(() => ({
  push: rs.fn(),
  replace: rs.fn(),
  prefetch: rs.fn(),
  back: rs.fn(),
}));

const mockParams = rs.hoisted(() => ({
  current: { agent_name: "researcher" },
}));

rs.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => "/agents/researcher/edit",
  useParams: () => mockParams.current,
}));

rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
rs.mock("@/core/api/fetcher", () => ({
  fetch: (input: RequestInfo, init?: RequestInit) =>
    globalThis.fetch(input, init),
  getCsrfHeaders: () => ({}),
  readCsrfCookie: () => null,
  isStateChangingMethod: () => false,
}));

type AgentDetail = {
  name: string;
  description: string;
  model: string | null;
  skills: string[] | null;
  tool_groups: string[] | null;
  soul: string;
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

function stubBackend(detail: AgentDetail) {
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

      if (url.endsWith("/api/agents/researcher")) {
        if (init?.method === "PUT") {
          return jsonResponse(detail);
        }
        return jsonResponse(detail);
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

function renderInProviders() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale="en-US">
        <MobileEditAgentPage />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

function detailWith(overrides: Partial<AgentDetail>): AgentDetail {
  return {
    name: "researcher",
    description: "Deep research with citations.",
    model: null,
    skills: null,
    tool_groups: null,
    soul: "You are a researcher.",
    ...overrides,
  };
}

function updatedBodies() {
  return fetchCalls
    .filter(
      (call) =>
        call.init?.method === "PUT" &&
        call.url.endsWith("/api/agents/researcher"),
    )
    .map((call) => parseBody(call.init));
}

afterEach(() => {
  cleanup();
  rs.unstubAllGlobals();
  rs.clearAllMocks();
  fetchCalls.length = 0;
  mockParams.current = { agent_name: "researcher" };
});

describe("whitelistState", () => {
  test("classifies the three states", () => {
    // No key in config.yaml — the agent inherits everything.
    expect(whitelistState(null)).toBe("inherit");
    expect(whitelistState(undefined)).toBe("inherit");
    // An explicit empty whitelist means the agent gets nothing.
    expect(whitelistState([])).toBe("none");
    // Anything else is a whitelist of exactly those entries.
    expect(whitelistState(["web-search"])).toBe("list");
  });
});

describe("Mobile agent edit screen", () => {
  test("null renders as 'inherits all', [] as 'none' — the two are different", async () => {
    stubBackend(detailWith({ skills: null, tool_groups: [] }));
    renderInProviders();

    const skills = await screen.findByTestId("mobile-agent-skills");
    expect(skills.getAttribute("data-whitelist-state")).toBe("inherit");
    expect(skills.textContent).toContain("Inherits all enabled skills");

    const toolGroups = screen.getByTestId("mobile-agent-tool-groups");
    expect(toolGroups.getAttribute("data-whitelist-state")).toBe("none");
    expect(toolGroups.textContent).toContain("No tool groups");
    // The whole point: the empty list must not read as "inherits all".
    expect(toolGroups.textContent).not.toContain("Inherits all tool groups");
  });

  test("a list renders every entry as a whitelist", async () => {
    stubBackend(
      detailWith({
        skills: ["data-analysis", "web-search"],
        tool_groups: ["browser"],
      }),
    );
    renderInProviders();

    const skills = await screen.findByTestId("mobile-agent-skills");
    expect(skills.getAttribute("data-whitelist-state")).toBe("list");
    expect(skills.textContent).toContain("data-analysis");
    expect(skills.textContent).toContain("web-search");
    expect(skills.textContent).not.toContain("Inherits all enabled skills");

    const toolGroups = screen.getByTestId("mobile-agent-tool-groups");
    expect(toolGroups.getAttribute("data-whitelist-state")).toBe("list");
    expect(toolGroups.textContent).toContain("browser");
  });

  test("the whitelists are read-only: no control to change them", async () => {
    stubBackend(
      detailWith({ skills: ["data-analysis"], tool_groups: ["web"] }),
    );
    renderInProviders();

    await screen.findByTestId("mobile-agent-skills");
    const section = screen.getByText(
      "Capability whitelists (read-only)",
    ).parentElement!;
    expect(section.querySelectorAll("button")).toHaveLength(0);
    expect(section.querySelectorAll("input")).toHaveLength(0);
    expect(section.querySelectorAll("textarea")).toHaveLength(0);
  });

  test("saving sends exactly the three editable keys", async () => {
    stubBackend(
      detailWith({
        description: "Old description",
        model: "gpt-5",
        skills: ["data-analysis"],
        tool_groups: null,
      }),
    );
    renderInProviders();

    const description = await screen.findByTestId("mobile-agent-description");
    await screen.findByRole("button", { name: "Save" });

    // Nothing is dirty yet, so the save button is inert.
    expect(
      screen.getByTestId("mobile-agent-save").hasAttribute("disabled"),
    ).toBe(true);

    await userEvent.clear(description);
    await userEvent.type(description, "New description");
    await userEvent.click(screen.getByTestId("mobile-agent-save"));

    await waitFor(() => {
      expect(updatedBodies()).toHaveLength(1);
    });
    // Exactly three keys: the whitelists above are read-only and must not ride
    // along in the request the backend merges by `model_fields_set`.
    expect(Object.keys(updatedBodies()[0]!).sort()).toEqual([
      "description",
      "model",
      "soul",
    ]);
    expect(updatedBodies()[0]).toEqual({
      description: "New description",
      // `model: "gpt-5"` is the fixture's model and stays untouched.
      model: "gpt-5",
      soul: "You are a researcher.",
    });

    // Back to the gallery on the public path.
    expect(mockRouter.push).toHaveBeenCalledWith("/agents");
  });

  test("leaving with unsaved changes asks first, and the change survives 'keep editing'", async () => {
    stubBackend(detailWith({ description: "Old description" }));
    renderInProviders();

    const description = await screen.findByTestId("mobile-agent-description");

    // Not dirty: the back button leaves without a question.
    await userEvent.click(screen.getByTestId("mobile-agent-edit-back"));
    expect(mockRouter.push).toHaveBeenCalledWith("/agents");

    mockRouter.push.mockClear();
    await userEvent.clear(description);
    await userEvent.type(description, "Edited");
    await userEvent.click(screen.getByTestId("mobile-agent-edit-back"));

    // The confirmation is a bottom sheet on a phone, not a centred dialog.
    expect(
      await screen.findByTestId("mobile-agent-discard-sheet"),
    ).toBeTruthy();
    expect(mockRouter.push).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(mockRouter.push).not.toHaveBeenCalled();
    expect(
      screen.getByTestId<HTMLInputElement>("mobile-agent-description").value,
    ).toBe("Edited");

    await userEvent.click(screen.getByTestId("mobile-agent-edit-back"));
    await userEvent.click(
      await screen.findByTestId("mobile-agent-discard-confirm"),
    );
    expect(mockRouter.push).toHaveBeenCalledWith("/agents");
  });
});
