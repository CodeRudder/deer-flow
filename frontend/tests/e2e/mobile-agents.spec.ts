import { devices, expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

/**
 * Mobile agent screens (T23/G1–G5, prototype ⑥).
 *
 * The suite runs on a phone UA, so every public path here is rewritten by the
 * middleware onto the `/m/*` tree (see `middleware.ts`); the *assertions* are on
 * the public address bar, because "the address bar never shows `/m/`" is the
 * property the whole split rests on (plan §1.2.1).
 *
 * Routing and data mocking follow `mobile-threads.spec.ts`: `devices["iPhone
 * 13"]` supplies the UA the middleware branches on, `defaultBrowserType` is
 * forced back to chromium (the iPhone presets are WebKit-only and this suite
 * installs chromium only), and `mockLangGraphAPI` answers everything that is
 * not the agent API. The agent routes below are registered *after* it, because
 * Playwright resolves the most recently registered matching route first — the
 * shared mock's `/api/agents` fixtures carry only a name and a description,
 * while these screens read `model` / `skills` / `tool_groups` too.
 */

test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

type MobileAgent = {
  name: string;
  description: string;
  model: string | null;
  tool_groups: string[] | null;
  skills: string[] | null;
  soul?: string;
};

const AGENTS: MobileAgent[] = [
  {
    name: "researcher",
    description: "Deep research with citations.",
    model: "gpt-5",
    tool_groups: ["web"],
    skills: ["data-analysis"],
    soul: "You are a researcher.",
  },
  {
    name: "weekly-report",
    description: "Writes the weekly report.",
    model: null,
    // `null` and `[]` mean opposite things once the agent runs, and the editor
    // is the only screen that can tell them apart.
    tool_groups: null,
    skills: [],
    soul: "",
  },
];

type AgentWrite = {
  method: string;
  url: string;
  body: Record<string, unknown>;
};

/** The wire body is JSON, so a non-string field is absent, not printed. */
function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * The agent API the two screens talk to, with the list held in memory so a
 * create/update is visible to the next read — which is what makes the "the
 * gallery is refreshed after creating" assertion meaningful.
 */
function mockAgentsAPI(page: Page, initial: MobileAgent[]) {
  let agents = initial.map((agent) => ({ ...agent }));
  const writes: AgentWrite[] = [];

  void page.route(/\/api\/agents\/check/, (route) => {
    const name = new URL(route.request().url()).searchParams.get("name") ?? "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        available: !agents.some((agent) => agent.name === name),
        name,
      }),
    });
  });

  void page.route(/\/api\/agents\/[^/]+$/, (route) => {
    const method = route.request().method();
    const name = decodeURIComponent(
      new URL(route.request().url()).pathname.split("/").at(-1) ?? "",
    );
    const existing = agents.find((agent) => agent.name === name);
    if (method === "GET") {
      return existing
        ? route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(existing),
          })
        : route.fulfill({
            status: 404,
            contentType: "application/json",
            body: JSON.stringify({ detail: "Agent not found" }),
          });
    }
    if (method === "PUT" || method === "DELETE") {
      if (method === "DELETE") {
        writes.push({ method, url: route.request().url(), body: {} });
        agents = agents.filter((agent) => agent.name !== name);
        return route.fulfill({ status: 204 });
      }
      const body = route.request().postDataJSON() as Record<string, unknown>;
      writes.push({ method, url: route.request().url(), body });
      const merged = { ...existing, ...body } as MobileAgent;
      agents = agents.map((agent) => (agent.name === name ? merged : agent));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(merged),
      });
    }
    return route.fallback();
  });

  void page.route(/\/api\/agents$/, (route) => {
    const method = route.request().method();
    if (method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ agents }),
      });
    }
    if (method === "POST") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      writes.push({ method, url: route.request().url(), body });
      const created = {
        name: asString(body.name),
        description: asString(body.description),
        model: typeof body.model === "string" ? body.model : null,
        tool_groups: null,
        skills: null,
        soul: asString(body.soul),
      };
      agents = [...agents, created];
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
    }
    return route.fallback();
  });

  return writes;
}

/** Two configured models, so the picker has something to offer. */
function mockModels(page: Page) {
  void page.route(/\/api\/models$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        models: [
          { id: "m1", name: "gpt-5", model: "gpt-5", display_name: "GPT-5" },
          {
            id: "m2",
            name: "claude-sonnet",
            model: "claude-sonnet",
            display_name: "Claude Sonnet",
          },
        ],
        vision_models: [],
        token_usage: { enabled: false },
      }),
    }),
  );
}

function cardFor(page: Page, name: string) {
  return page.getByTestId("mobile-agent-card").filter({ hasText: name });
}

/**
 * Apple's floor, measured on the rendered box rather than read off a class
 * name — the plan sets 44×44 and the whole mobile tree is sized with `size-11`
 * / `min-h-11` / `min-h-12` to clear it.
 */
async function expectTouchTarget(page: Page, testId: string) {
  const box = await page.getByTestId(testId).boundingBox();
  expect(box, `${testId} has no box`).not.toBeNull();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.height).toBeGreaterThanOrEqual(44);
}

test.describe("Mobile agent gallery (G1)", () => {
  test("renders the agents as a single column of cards", async ({ page }) => {
    mockLangGraphAPI(page);
    mockAgentsAPI(page, AGENTS);
    await page.goto("/agents");

    await expect(page.getByTestId("mobile-agent-card")).toHaveCount(2, {
      timeout: 15_000,
    });
    await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();

    // One card per row: no card shares a row with another, which is what the
    // desktop grid does at ≥640px and what a phone must not.
    const tops = await page
      .getByTestId("mobile-agent-card")
      .evaluateAll((cards) =>
        cards.map((card) => card.getBoundingClientRect().top),
      );
    expect(tops).toHaveLength(2);
    expect(tops[1]!).toBeGreaterThan(tops[0]!);

    await expect(cardFor(page, "researcher")).toContainText(
      "Deep research with citations.",
    );
    await expect(cardFor(page, "researcher")).toContainText("gpt-5");

    // The lit tab stays the agents tab; the bar is the shell's, not the page's.
    const tabBar = page.getByRole("navigation");
    await expect(tabBar.getByRole("link", { name: "Agents" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("an empty account gets the empty state, not a blank page", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    mockAgentsAPI(page, []);
    await page.goto("/agents");

    await expect(page.getByTestId("mobile-agents-empty")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("No custom agents yet")).toBeVisible();
    await expect(page.getByTestId("mobile-agents-loading")).toHaveCount(0);
  });

  test("every card control clears the 44px touch floor", async ({ page }) => {
    mockLangGraphAPI(page);
    mockAgentsAPI(page, AGENTS);
    await page.goto("/agents");

    await expect(page.getByTestId("mobile-agent-card")).toHaveCount(2, {
      timeout: 15_000,
    });
    await expectTouchTarget(page, "mobile-new-agent");
    await expectTouchTarget(page, "mobile-agent-chat-researcher");
    await expectTouchTarget(page, "mobile-agent-edit-researcher");
    await expectTouchTarget(page, "mobile-agent-delete-researcher");
  });

  test("360px stays free of horizontal scrolling", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 });
    mockLangGraphAPI(page);
    mockAgentsAPI(page, AGENTS);
    await page.goto("/agents");
    await expect(page.getByTestId("mobile-agent-card")).toHaveCount(2, {
      timeout: 15_000,
    });

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});

test.describe("Mobile agent chat entry (G2)", () => {
  test("tapping a card opens that agent's chat without /m/ in the address bar", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    mockAgentsAPI(page, AGENTS);
    await page.goto("/agents");

    await page.getByTestId("mobile-agent-chat-researcher").click();

    // The public path, same address as the desktop's agent chat: the
    // middleware is what lands a phone on the mobile chat tree.
    await expect(page).toHaveURL("/workspace/agents/researcher/chats/new");
    expect(new URL(page.url()).pathname).toBe(
      "/workspace/agents/researcher/chats/new",
    );
    expect(page.url()).not.toContain("/m/");
  });
});

test.describe("Mobile agent creation (G3, G4)", () => {
  test("the ＋ sheet offers both paths and routes to the public addresses", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    mockAgentsAPI(page, AGENTS);
    await page.goto("/agents");

    await page.getByTestId("mobile-new-agent").click();
    const sheet = page.getByTestId("mobile-agent-create-sheet");
    await expect(sheet).toBeVisible();
    await expect(
      sheet.getByRole("button", { name: /Create manually/ }),
    ).toBeVisible();
    await expect(
      sheet.getByRole("button", { name: /Create through chat/ }),
    ).toBeVisible();

    // 对话式创建 is a conversation, so it keeps the desktop path; the manual
    // form is a screen of its own on a phone.
    await sheet.getByRole("button", { name: /Create through chat/ }).click();
    await expect(page).toHaveURL("/workspace/agents/new");
    expect(page.url()).not.toContain("/m/");
  });

  test("creating an agent posts the form and refreshes the gallery", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    mockModels(page);
    const writes = mockAgentsAPI(page, []);
    await page.goto("/agents");

    await expect(page.getByTestId("mobile-agents-empty")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId("mobile-new-agent").click();
    await page.getByRole("button", { name: /Create manually/ }).click();
    await expect(page).toHaveURL("/agents/new");

    await page.getByTestId("mobile-agent-name").fill("weekly-report");
    await page
      .getByTestId("mobile-agent-description")
      .fill("Writes the weekly report.");
    await page.getByTestId("mobile-agent-model-trigger").click();
    await page.getByTestId("mobile-agent-model-claude-sonnet").click();

    await page.getByTestId("mobile-agent-create-submit").click();

    // Back on the gallery, on the public path.
    await expect(page).toHaveURL("/agents");
    expect(page.url()).not.toContain("/m/");

    // The list was refreshed by the mutation's own invalidation, so the new
    // agent is on it without a reload.
    await expect(page.getByTestId("mobile-agent-card")).toHaveCount(1, {
      timeout: 15_000,
    });
    await expect(cardFor(page, "weekly-report")).toBeVisible();

    const create = writes.find((write) => write.method === "POST");
    expect(create?.body).toEqual({
      name: "weekly-report",
      description: "Writes the weekly report.",
      model: "claude-sonnet",
      soul: "",
    });
  });

  test("an invalid name is refused before anything is created", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    mockModels(page);
    const writes = mockAgentsAPI(page, AGENTS);
    await page.goto("/agents/new");

    await page.getByTestId("mobile-agent-name").fill("has space");
    await page.getByTestId("mobile-agent-create-submit").click();

    await expect(page.getByRole("alert")).toContainText("Invalid name");
    expect(writes.filter((write) => write.method === "POST")).toHaveLength(0);
  });
});

test.describe("Mobile agent editing (G5)", () => {
  test("the editor spells out all three whitelist states", async ({ page }) => {
    mockLangGraphAPI(page);
    mockModels(page);
    mockAgentsAPI(page, AGENTS);
    await page.goto("/agents");

    await page.getByTestId("mobile-agent-edit-researcher").click();
    await expect(page).toHaveURL("/agents/researcher/edit");
    expect(page.url()).not.toContain("/m/");

    // `null` = inherit all, `[]` = none, list = exactly those entries.
    await expect(page.getByTestId("mobile-agent-skills")).toContainText(
      "data-analysis",
    );
    await expect(page.getByTestId("mobile-agent-tool-groups")).toContainText(
      "Inherits all tool groups",
    );
  });

  test("the empty whitelist reads as 'none', not as 'inherits all'", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    mockModels(page);
    mockAgentsAPI(page, AGENTS);
    await page.goto("/agents");

    await page.getByTestId("mobile-agent-edit-weekly-report").click();
    await expect(page).toHaveURL("/agents/weekly-report/edit");

    const skills = page.getByTestId("mobile-agent-skills");
    await expect(skills).toHaveAttribute("data-whitelist-state", "none");
    await expect(skills).toContainText("No skills");
    await expect(skills).not.toContainText("Inherits all enabled skills");
  });

  test("saving sends only the three editable fields", async ({ page }) => {
    mockLangGraphAPI(page);
    mockModels(page);
    const writes = mockAgentsAPI(page, AGENTS);
    await page.goto("/agents");

    await page.getByTestId("mobile-agent-edit-researcher").click();
    await expect(page).toHaveURL("/agents/researcher/edit");
    await page.getByTestId("mobile-agent-description").fill("Updated copy.");
    await page.getByTestId("mobile-agent-save").click();

    await expect(page).toHaveURL("/agents");
    const update = writes.find((write) => write.method === "PUT");
    expect(Object.keys(update!.body).sort()).toEqual([
      "description",
      "model",
      "soul",
    ]);
    expect(update!.body).toEqual({
      description: "Updated copy.",
      model: "gpt-5",
      soul: "You are a researcher.",
    });
  });

  test("the editor's controls clear the 44px touch floor", async ({ page }) => {
    mockLangGraphAPI(page);
    mockModels(page);
    mockAgentsAPI(page, AGENTS);
    await page.goto("/agents/researcher/edit");

    await expect(page.getByTestId("mobile-agent-save")).toBeVisible({
      timeout: 15_000,
    });
    await expectTouchTarget(page, "mobile-agent-edit-back");
    await expectTouchTarget(page, "mobile-agent-model-trigger");
    await expectTouchTarget(page, "mobile-agent-save");
  });
});
