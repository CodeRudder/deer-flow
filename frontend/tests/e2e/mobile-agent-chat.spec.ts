import { devices, expect, test, type Page } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

/**
 * The two mobile screens T23 could not build: the agent chat (G2) and the
 * chat-based creation conversation (G4).
 *
 * Both were **missing routes**, not broken ones: the agent card's "start
 * chatting" link and the create sheet's "create through chat" option point at
 * the desktop's public paths (`/workspace/agents/<name>/chats/new`,
 * `/workspace/agents/new`), and a phone UA had no mobile tree to rewrite them
 * onto — every tap landed on a 404. So every test here starts from the public
 * address the entry point uses, and the assertions are on the address bar as
 * much as on the screen (plan §1.2.1: `/m/` must never appear there).
 *
 * The agent chat also proves the `agent_name` wiring end to end: the run-stream
 * request must carry it, because that single field is what makes the backend
 * run the *agent* instead of the default assistant. `use-chat-page.test.tsx`
 * covers the hook in isolation; this covers the assembled screen.
 *
 * Routing and data mocking follow the rest of the mobile suite (`devices
 * ["iPhone 13"]` for the UA the middleware branches on, `defaultBrowserType`
 * forced back to chromium because only chromium is installed here), plus the
 * agent routes registered *after* `mockLangGraphAPI` — the shared mock's
 * `/api/agents/*` catch-all answers 404 to anything not in its fixture list.
 */

test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const AGENT_NAME = "researcher";
const AGENT_CHAT_PATH = `/workspace/agents/${AGENT_NAME}/chats/new`;
const CREATE_PATH = "/workspace/agents/new";
const BOOTSTRAP_AGENT_NAME = "weekly-report";

type StreamBody = {
  input?: { messages?: Array<{ content?: unknown }> };
  context?: Record<string, unknown>;
};

/** The text of the human message in a run-stream request. */
function submittedText(body: StreamBody): string {
  const content = body.input?.messages?.at(-1)?.content;
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .map((block) =>
        typeof block === "object" &&
        block !== null &&
        "text" in block &&
        typeof block.text === "string"
          ? block.text
          : "",
      )
      .join("");
  }
  return "";
}

/** Records the JSON body of every run-stream request. */
function recordStreamBodies(page: Page) {
  const bodies: StreamBody[] = [];
  void page.route("**/runs/stream", (route) => {
    bodies.push(route.request().postDataJSON() as StreamBody);
    return route.fallback();
  });
  return bodies;
}

/** The agent the gallery card would have opened. */
function mockAgentAPI(page: Page) {
  void page.route(/\/api\/agents\/[^/]+$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        name: AGENT_NAME,
        description: "Deep research with citations.",
        model: "gpt-5",
        tool_groups: ["web"],
        skills: ["data-analysis"],
        soul: "You are a researcher.",
      }),
    }),
  );
}

/** Name-availability check for the creation flow. */
function mockAgentNameCheck(page: Page, available: boolean) {
  void page.route(/\/api\/agents\/check/, (route) => {
    const name = new URL(route.request().url()).searchParams.get("name") ?? "";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ available, name }),
    });
  });
}

function composerTextarea(page: Page) {
  return page.getByTestId("mobile-composer").locator("textarea");
}

test.describe("Mobile agent chat (G2)", () => {
  test("the agent's chat renders on a phone, on the public address", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    mockAgentAPI(page);

    await page.goto(AGENT_CHAT_PATH);

    // Before T27 this path 404'd for a phone: no mobile route matched the
    // middleware's rewrite.
    await expect(page.getByTestId("mobile-composer")).toBeVisible({
      timeout: 15_000,
    });

    // The header names the *agent*, not the thread (a fresh thread has no
    // title at all).
    await expect(page.getByTestId("mobile-chat-title")).toHaveText(AGENT_NAME);

    // The public address, unchanged by the internal rewrite.
    expect(new URL(page.url()).pathname).toBe(AGENT_CHAT_PATH);
    expect(page.url()).not.toContain("/m/");
  });

  test("the screen is full-bleed: no tab bar under the composer", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    mockAgentAPI(page);

    await page.goto(AGENT_CHAT_PATH);
    await expect(page.getByTestId("mobile-composer")).toBeVisible({
      timeout: 15_000,
    });

    // T17: the chat screen is in `(fullbleed)` because its composer owns the
    // bottom edge; a tab bar underneath would stack two bars.
    await expect(page.getByRole("navigation")).toHaveCount(0);
  });

  test("back goes to the agents gallery, not the thread list", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    mockAgentAPI(page);
    await page.goto(AGENT_CHAT_PATH);
    await expect(page.getByTestId("mobile-composer")).toBeVisible({
      timeout: 15_000,
    });

    await page.getByTestId("mobile-chat-back").click();

    // `/agents` is the mobile gallery (the screen the card was tapped on); the
    // plain chat page's back button, `/workspace`, would be the wrong place.
    await expect(page).toHaveURL("/agents");
    await expect(page.getByTestId("mobile-agent-card")).toHaveCount(1, {
      timeout: 15_000,
    });
  });

  test("a sent message runs the agent and lands on the agent's own chat address", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    mockAgentAPI(page);
    const bodies = recordStreamBodies(page);

    await page.goto(AGENT_CHAT_PATH);
    await expect(page.getByTestId("mobile-composer")).toBeVisible({
      timeout: 15_000,
    });

    await composerTextarea(page).fill("Summarise this quarter");
    await page.getByTestId("mobile-composer-send").click();

    await expect
      .poll(() => bodies.length, { timeout: 10_000 })
      .toBeGreaterThan(0);

    // `agent_name` on the run — without it the backend answers as the default
    // assistant and the whole screen is pointless.
    expect(bodies[0]?.context?.agent_name).toBe(AGENT_NAME);
    expect(submittedText(bodies[0]!)).toBe("Summarise this quarter");

    // The new thread's address is written by `history.replaceState`, which no
    // middleware ever sees — so it is the page that has to get it right: the
    // desktop tree's public path, one level under the agent, never `/m/…`.
    await page.waitForURL(
      `**/workspace/agents/${AGENT_NAME}/chats/${MOCK_THREAD_ID}`,
    );
    expect(page.url()).not.toContain("/m/");
  });
});

test.describe("Chat-based agent creation (G4)", () => {
  test("the name step starts the conversation the desktop starts", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    mockAgentNameCheck(page, true);
    const bodies = recordStreamBodies(page);

    await page.goto(CREATE_PATH);

    // The name step, not a chat — the conversation only starts once a name has
    // been checked against the backend.
    await expect(page.getByTestId("mobile-agent-create-name")).toBeVisible({
      timeout: 15_000,
    });
    expect(new URL(page.url()).pathname).toBe(CREATE_PATH);
    expect(page.url()).not.toContain("/m/");

    await page
      .getByTestId("mobile-agent-create-name")
      .fill(BOOTSTRAP_AGENT_NAME);
    await page.getByTestId("mobile-agent-create-name-submit").click();

    await expect
      .poll(() => bodies.length, { timeout: 10_000 })
      .toBeGreaterThan(0);

    const context = bodies[0]?.context ?? {};
    // The creation run: a flash run flagged as a bootstrap, addressed to the
    // name the user just chose — the desktop page's exact context.
    expect(context.agent_name).toBe(BOOTSTRAP_AGENT_NAME);
    expect(context.is_bootstrap).toBe(true);
    expect(context.mode).toBe("flash");

    // The bootstrap copy is `t.agents.nameStepBootstrapMessage` (the desktop's
    // key, not a mobile-only rewrite of it), with `{name}` substituted.
    const bootstrap = submittedText(bodies[0]!);
    expect(bootstrap).toContain(BOOTSTRAP_AGENT_NAME);
    expect(bootstrap.toLowerCase()).toContain("agent");

    // Still on the public create address; the conversation is the screen.
    expect(new URL(page.url()).pathname).toBe(CREATE_PATH);
    expect(page.url()).not.toContain("/m/");
    await expect(page.getByTestId("mobile-agent-create-input")).toBeVisible();
  });

  test("a taken name never starts a conversation", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [] });
    mockAgentNameCheck(page, false);
    const bodies = recordStreamBodies(page);

    await page.goto(CREATE_PATH);
    await expect(page.getByTestId("mobile-agent-create-name")).toBeVisible({
      timeout: 15_000,
    });

    await page.getByTestId("mobile-agent-create-name").fill("researcher");
    await page.getByTestId("mobile-agent-create-name-submit").click();

    await expect(page.getByRole("alert")).toBeVisible();
    expect(bodies).toHaveLength(0);
    await expect(page.getByTestId("mobile-agent-create-input")).toHaveCount(0);
  });
});
