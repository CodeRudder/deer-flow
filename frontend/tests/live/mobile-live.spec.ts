import {
  devices,
  expect,
  request,
  test,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";

/**
 * Live mobile smoke suite (see `playwright.live.config.ts` for why it exists).
 *
 * Every test here answers one question the mocked suite cannot: **does the real
 * app, against the real gateway, actually show the user's data?** The report was
 * "移动端全部是空白页面，没有加载到已有的数据" — a shell with no threads and no
 * transcript. Under `page.route()` mocks that is unobservable: the mock always
 * answers, always in the shape the page expects, so a wrong endpoint, a renamed
 * response field, a silent 401/500, or a `gateway_unavailable` state all stay
 * green while the real app paints nothing.
 *
 * Each test therefore asserts three things that together mean "not blank":
 *
 *   1. the screen rendered its real data (rows / the last human message / the
 *      signed-in email),
 *   2. nothing 4xx/5xx'd on the way — a silent failure makes a component render
 *      its *empty state*, which reads as "no data" rather than "broken",
 *   3. nothing threw in the console or as an uncaught page error.
 *
 * Plus the invariant that has already regressed once on this branch: the
 * address bar must never show the internal `/m/` prefix.
 */

const BASE_URL = process.env.DEER_FLOW_E2E_BASE_URL ?? "http://localhost:2026";
const EMAIL = process.env.DEER_FLOW_E2E_EMAIL;
const PASSWORD = process.env.DEER_FLOW_E2E_PASSWORD;

/**
 * Missing credentials fail loudly rather than skipping. This suite is run
 * deliberately (it is not part of `pnpm test:e2e`), so a silent skip would be
 * indistinguishable from a pass. Same convention as `model-settings.spec.ts`.
 */
if (!EMAIL || !PASSWORD) {
  throw new Error(
    "缺少登录凭据。请设置环境变量，例如：\n" +
      "  DEER_FLOW_E2E_EMAIL='admin@sz-jlc.com' DEER_FLOW_E2E_PASSWORD='...' pnpm test:live\n" +
      "（需要先启动真实服务：make dev 或 make start）",
  );
}

const IPHONE_UA = devices["iPhone 13"].userAgent;
const DESKTOP_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";

type PageSignals = {
  consoleErrors: string[];
  pageErrors: string[];
  apiFailures: string[];
};

/**
 * Attaches the three listeners *before* navigation — a listener added
 * afterwards misses exactly the failures being hunted, the ones during first
 * paint.
 */
function watch(page: Page): PageSignals {
  const signals: PageSignals = {
    consoleErrors: [],
    pageErrors: [],
    apiFailures: [],
  };
  page.on("console", (message) => {
    if (message.type() === "error") {
      signals.consoleErrors.push(message.text().slice(0, 300));
    }
  });
  page.on("pageerror", (error) => {
    signals.pageErrors.push(String(error).slice(0, 300));
  });
  page.on("response", (response) => {
    const url = response.url();
    // Only the app's own API. A missing favicon is not a data failure, and
    // `_rsc` requests are Next's prefetches — routinely aborted, never carrying
    // data the first paint needs.
    if (!url.includes("/api/") || url.includes("_rsc=")) {
      return;
    }
    if (response.status() >= 400) {
      signals.apiFailures.push(
        `${response.status()} ${response.request().method()} ${url.slice(0, 140)}`,
      );
    }
  });
  return signals;
}

/** A blank screen and an error screen look alike in a screenshot; this separates them. */
function expectHealthy(signals: PageSignals, where: string) {
  expect(signals.apiFailures, `${where}: 有接口 4xx/5xx，页面会因此渲染成空的`)
    .toEqual([]);
  expect(signals.consoleErrors, `${where}: 控制台报错`).toEqual([]);
  expect(signals.pageErrors, `${where}: 页面未捕获异常`).toEqual([]);
}

let cookies: Awaited<
  ReturnType<Awaited<ReturnType<typeof request.newContext>>["storageState"]>
>["cookies"] = [];
/** A thread that exists for this user, discovered from the app's own endpoint. */
let threadId: string | null = null;
/** The last human message in that thread — the text the transcript must show. */
let lastHumanText: string | null = null;

test.describe("Live mobile smoke", () => {
  test.beforeAll(async () => {
    const api = await request.newContext({ baseURL: BASE_URL });

    const login = await api.post("/api/v1/auth/login/local", {
      form: { username: EMAIL, password: PASSWORD },
      headers: { Origin: BASE_URL },
    });
    expect(
      login.status(),
      `登录失败（HTTP ${login.status()}）—— 检查 DEER_FLOW_E2E_EMAIL / DEER_FLOW_E2E_PASSWORD 与 ${BASE_URL}`,
    ).toBe(200);
    cookies = (await api.storageState()).cookies;

    const search = await api.post("/api/langgraph/threads/search", {
      data: { limit: 5, offset: 0, sortBy: "updated_at", sortOrder: "desc" },
      headers: { Origin: BASE_URL },
    });
    if (search.ok()) {
      const body = (await search.json()) as Array<{ thread_id?: string }>;
      threadId = body.find((thread) => thread.thread_id)?.thread_id ?? null;
    }

    if (threadId) {
      const state = await api.get(`/api/langgraph/threads/${threadId}/state`, {
        headers: { Origin: BASE_URL },
      });
      if (state.ok()) {
        const values = (await state.json()) as {
          values?: { messages?: Array<{ type?: string; content?: unknown }> };
        };
        const humans = (values.values?.messages ?? []).filter(
          (message) => message.type === "human" && typeof message.content === "string",
        );
        const text = humans.at(-1)?.content;
        lastHumanText = typeof text === "string" ? text.slice(0, 40) : null;
      }
    }

    await api.dispose();
  });

  /** A phone context carrying the real session cookie. */
  async function openPhone(
    browser: Browser,
  ): Promise<{ context: BrowserContext; page: Page; signals: PageSignals }> {
    const context = await browser.newContext({
      ...devices["iPhone 13"],
      userAgent: IPHONE_UA,
    });
    await context.addCookies(cookies);
    const page = await context.newPage();
    const signals = watch(page);
    return { context, page, signals };
  }

  test("会话列表加载真实数据，地址栏不含 /m/", async ({ browser }) => {
    test.skip(!threadId, "该实例没有任何会话，无法验证「有数据却显示为空」");
    const { context, page, signals } = await openPhone(browser);

    await page.goto("/workspace", { waitUntil: "domcontentloaded" });
    // Wait for content rather than asserting straight after paint — the app
    // fetches after the shell renders, and asserting too early is precisely how
    // a "blank page" gets reported.
    await expect(page.getByTestId("mobile-thread-row").first()).toBeVisible({
      timeout: 20_000,
    });

    expect(new URL(page.url()).pathname).not.toContain("/m/");
    expectHealthy(signals, "/workspace");
    await context.close();
  });

  test("点开会话能看到真实会话内容", async ({ browser }) => {
    test.skip(!threadId || !lastHumanText, "没有可用作断言的会话内容");
    const { context, page, signals } = await openPhone(browser);

    await page.goto(`/workspace/chats/${threadId}`, {
      waitUntil: "domcontentloaded",
    });
    // Scoped to the transcript: the header carries the auto-generated thread
    // title, which for a short first message is the message itself — an
    // unscoped `getByText` matches both and trips strict mode.
    await expect(
      page.getByRole("log").getByText(lastHumanText!, { exact: false }),
    ).toBeVisible({ timeout: 20_000 });

    expect(new URL(page.url()).pathname).not.toContain("/m/");
    expectHealthy(signals, "chat");
    await context.close();
  });

  test("设置页显示账号信息，不是空白页", async ({ browser }) => {
    const { context, page, signals } = await openPhone(browser);

    await page.goto("/settings", { waitUntil: "domcontentloaded" });
    await expect(page.getByText(EMAIL, { exact: false })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByRole("button", { name: /sign out|退出登录/i })).toBeVisible();

    expect(new URL(page.url()).pathname).not.toContain("/m/");
    expectHealthy(signals, "/settings");
    await context.close();
  });

  test("智能体页至少渲染出屏幕本身（当前为占位页）", async ({ browser }) => {
    const { context, page, signals } = await openPhone(browser);

    await page.goto("/agents", { waitUntil: "domcontentloaded" });
    // Now a placeholder screen: this asserts the route resolves and paints its
    // own root, not that the gallery exists. Tighten when T9 lands.
    await expect(page.locator("h1")).toBeVisible({ timeout: 20_000 });

    expect(new URL(page.url()).pathname).not.toContain("/m/");
    expectHealthy(signals, "/agents");
    await context.close();
  });

  test("桌面 UA 仍走桌面树", async ({ browser }) => {
    const context = await browser.newContext({ userAgent: DESKTOP_UA });
    await context.addCookies(cookies);
    const page = await context.newPage();

    await page.goto("/workspace", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("mobile-thread-row")).toHaveCount(0);

    await context.close();
  });
});
