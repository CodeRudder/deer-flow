import {
  devices,
  expect,
  test,
  type Locator,
  type Page,
} from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

/**
 * C11 on the phone: the plan-mode todo panel above the composer (T20), the
 * progress reading its header carries (T28), and the elapsed times beside the
 * tasks (T29).
 *
 * The panel is the desktop's `TodoList`, reused as-is; what this spec exists to
 * prove is that the phone page renders it, where, that the mobile-only CSS
 * gives it a working toggle and no sideways overflow, and that the elapsed
 * times the helper agent's stamps imply actually reach the screen. Everything
 * is mocked, so it says nothing about whether the helper agent really
 * publishes todos — the state shape below is copied from what the desktop page
 * already reads (`thread.values.todos`).
 *
 * Same harness as `mobile-chat.spec.ts`: the iPhone 13 preset supplies the
 * User-Agent the middleware branches on (`defaultBrowserType` is forced back to
 * chromium because the iPhone presets are WebKit-only and this suite installs
 * chromium only).
 */
test.use({
  ...devices["iPhone 13"],
  defaultBrowserType: "chromium",
});

const CHAT_PATH = `/workspace/chats/${MOCK_THREAD_ID}`;

const HUMAN_MESSAGE = {
  type: "human",
  id: "msg-human-1",
  content: [{ type: "text", text: "Plan the launch" }],
};

const AI_REPLY = { type: "ai", id: "msg-ai-1", content: "Here is the plan." };

const MINUTE = 60_000;

type Todo = {
  content: string;
  status: "pending" | "in_progress" | "completed";
  /** ISO 8601, stamped by the backend on the way into `in_progress`. */
  started_at?: string;
  /** ISO 8601, stamped by the backend on the way into `completed`. */
  completed_at?: string;
};

/** An ISO instant `ms` in the past, read as the fixture is built. */
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

/**
 * Five todos, so the list is longer than the panel's `h-28` body and any
 * "only the first row survived" bug is visible, and so the mounted/finished
 * states are all represented.
 */
const TODOS: Todo[] = [
  { content: "Draft the outline", status: "completed" },
  { content: "Collect the numbers", status: "in_progress" },
  { content: "Write the summary", status: "pending" },
  { content: "Send it for review", status: "pending" },
  { content: "File the follow-ups", status: "pending" },
];

/**
 * The same five with a second one done, so the counts read `2 / 1 / 2`: the
 * prototype's three buckets at once, none of them equal to another or to the
 * list length.
 */
const TODOS_TWO_DONE: Todo[] = TODOS.map((todo, index) =>
  index === 2 ? { ...todo, status: "completed" } : todo,
);

/**
 * Two running tasks: the pair the "now" row has to take turns between. The
 * names differ in length as well as in text, so a rail that failed to slide
 * would not accidentally look right.
 */
const TODOS_TWO_RUNNING: Todo[] = TODOS.map((todo, index) =>
  index === 2 ? { ...todo, status: "in_progress" } : todo,
);

/** The running names of `TODOS_TWO_RUNNING`, in the order they run. */
const RUNNING = ["Collect the numbers", "Write the summary"] as const;

/**
 * The same five carrying the stamps the backend writes: a finished task gets
 * both ends, a running one only a start, and a waiting one nothing — which is
 * exactly the shape that has to render as "12m", "3m" and no column at all.
 *
 * The offsets are read when the fixture is built, so the readings on screen
 * are the ones written here for as long as the spec takes to assert them.
 */
const TODOS_WITH_TIME: Todo[] = [
  {
    content: "Draft the outline",
    status: "completed",
    started_at: ago(12 * MINUTE),
    completed_at: ago(0),
  },
  {
    content: "Collect the numbers",
    status: "in_progress",
    started_at: ago(3 * MINUTE),
  },
  { content: "Write the summary", status: "pending" },
  { content: "Send it for review", status: "pending" },
  { content: "File the follow-ups", status: "pending" },
];

/**
 * Two running tasks with different starts, so the row that takes turns between
 * them has two distinguishable numbers to carry — `5m` for the first, `1m` for
 * the second.
 */
const TODOS_TWO_RUNNING_WITH_TIME: Todo[] = [
  {
    content: "Collect the numbers",
    status: "in_progress",
    started_at: ago(5 * MINUTE),
  },
  {
    content: "Write the summary",
    status: "in_progress",
    started_at: ago(MINUTE),
  },
  { content: "Draft the outline", status: "completed", completed_at: ago(0) },
  { content: "Send it for review", status: "pending" },
  { content: "File the follow-ups", status: "pending" },
];

/** The same five with no stamps at all — every thread written before T29. */
const TODOS_WITHOUT_TIME: Todo[] = TODOS;

/**
 * Answers the thread-state reads with the todos the helper agent published.
 *
 * Registered *after* `mockLangGraphAPI`, which Playwright matches in reverse
 * registration order — the shared mock fixture has no `todos` field, and this
 * is the only place C11's state can come from. `useStream` reads the same
 * thread through `/state` (the initial read) and `/history` (paging); both
 * carry `values`, so both are answered with the same payload.
 */
function mockThreadTodos(page: Page, todos: Todo[]) {
  const values = {
    title: "Launch plan",
    messages: [HUMAN_MESSAGE, AI_REPLY],
    artifacts: [],
    todos,
  };

  void page.route("**/api/langgraph/threads/*/state", (route) => {
    if (route.request().method() !== "GET") {
      return route.fallback();
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        values,
        next: [],
        metadata: {},
        created_at: "2025-01-01T00:00:00Z",
      }),
    });
  });

  void page.route("**/api/langgraph/threads/*/history", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          values,
          next: [],
          metadata: {},
          created_at: "2025-01-01T00:00:00Z",
          parent_config: null,
        },
      ]),
    }),
  );
}

/** Opens the chat with a plan the helper agent wrote. */
async function openPlannedChat(page: Page, todos: Todo[]) {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Launch plan",
        messages: [HUMAN_MESSAGE, AI_REPLY],
      },
    ],
  });
  mockThreadTodos(page, todos);
  await page.goto(CHAT_PATH);
  await expect(page.getByTestId("mobile-composer")).toBeVisible({
    timeout: 15_000,
  });
}

/** The panel's two halves, by the same hooks the CSS uses. */
function panel(page: Page) {
  return {
    wrapper: page.getByTestId("mobile-todo-list"),
    header: page.locator(".mobile-todo-panel > header"),
    body: page.locator(".mobile-todo-panel > main"),
  };
}

async function height(locator: Locator) {
  const box = await locator.boundingBox();
  return box?.height ?? 0;
}

/** Horizontal overflow of the document, in CSS pixels. */
async function horizontalOverflow(page: Page) {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
}

/** The reading beside each list row, or `null` where the row draws none. */
async function rowDurations(page: Page) {
  return page.$$eval(".mobile-todo-panel li", (items) =>
    items.map(
      (item) =>
        item.querySelector('[data-testid="todo-duration"]')?.textContent ??
        null,
    ),
  );
}

/** The elapsed time at the right-hand end of the running-task row. */
async function nowDuration(page: Page) {
  const node = page.getByTestId("todo-now-duration");
  return (await node.count()) === 0
    ? null
    : ((await node.textContent()) ?? null);
}

/**
 * The running-task name actually on screen, read off the layout: the rail
 * holds every running name (plus the copy it loops onto), and the window shows
 * the one block that is inside it. `null` mid-slide, when no block is wholly
 * inside yet — poll rather than read once.
 */
async function visibleName(page: Page) {
  return page.evaluate(() => {
    const window = document.querySelector<HTMLElement>(
      '[data-testid="todo-now"] > span:last-child',
    );
    if (!window) {
      return null;
    }
    const box = window.getBoundingClientRect();
    const shown = [...window.querySelectorAll("span")].find((block) => {
      const blockBox = block.getBoundingClientRect();
      return blockBox.top >= box.top - 1 && blockBox.bottom <= box.bottom + 1;
    });
    return shown?.textContent ?? null;
  });
}

test.describe("Mobile chat todo panel", () => {
  test("renders the thread's todos above the composer, folded", async ({
    page,
  }) => {
    await openPlannedChat(page, TODOS);

    const { wrapper, body } = panel(page);
    await expect(wrapper).toBeVisible();

    // Every todo, once, inside the list. The panel caps its own height and
    // scrolls, so the count is asserted on the DOM rather than on what fits —
    // and it is asserted against the body rather than the whole panel,
    // because the running-task row above it names one of them as well.
    for (const todo of TODOS) {
      await expect(body.getByText(todo.content)).toHaveCount(1);
    }
    await expect(wrapper.locator("li")).toHaveCount(TODOS.length);

    // Above the composer, inside the composer's horizontal band.
    const panelBox = (await wrapper.boundingBox())!;
    const composerBox = (await page
      .getByTestId("mobile-composer")
      .boundingBox())!;
    expect(panelBox.y + panelBox.height).toBeLessThanOrEqual(composerBox.y);
    expect(panelBox.x).toBeGreaterThanOrEqual(composerBox.x);
    expect(panelBox.x + panelBox.width).toBeLessThanOrEqual(
      composerBox.x + composerBox.width,
    );

    // Folded on arrival, exactly like the desktop: the body is height 0, so
    // only the header bar is on screen.
    expect(await height(body)).toBeLessThan(20);
  });

  test("the header counts done, running and waiting", async ({ page }) => {
    await openPlannedChat(page, TODOS_TWO_DONE);

    const { header, body } = panel(page);
    const counts = page.getByTestId("todo-counts");
    const bucket = (index: number) => counts.locator("span").nth(index);

    // Two done, one running, two waiting — each on its own, so a count that
    // was really the list length (or another bucket) could not pass.
    await expect(bucket(0)).toHaveText("✓ 2");
    await expect(bucket(1)).toHaveText("◐ 1");
    await expect(bucket(2)).toHaveText("○ 2");

    // Folded — the state a phone opens on — the counts are on screen. They
    // sit inside the header bar (so the 44px toggle covers them, rather than
    // being a pair of controls of their own), in its right half, and before
    // the chevron, which stays the last thing on the row.
    expect(await height(body)).toBeLessThan(20);
    await expect(counts).toBeVisible();
    const countsBox = (await counts.boundingBox())!;
    const headerBox = (await header.boundingBox())!;
    expect(countsBox.x).toBeGreaterThan(headerBox.x + headerBox.width / 2);
    expect(countsBox.y).toBeGreaterThanOrEqual(headerBox.y);
    expect(countsBox.y + countsBox.height).toBeLessThanOrEqual(
      headerBox.y + headerBox.height,
    );
    const chevronBox = (await header.locator("svg").last().boundingBox())!;
    expect(countsBox.x + countsBox.width).toBeLessThanOrEqual(chevronBox.x);

    // `font-variant-numeric: tabular-nums`, so ticking a todo off does not
    // shuffle the digits — the computed value, not a class name.
    expect(
      await counts.evaluate(
        (element) => getComputedStyle(element).fontVariantNumeric,
      ),
    ).toContain("tabular-nums");

    // Opening the list does not take the counts away: they are not a
    // folded-only affordance.
    await header.click();
    await expect
      .poll(() => height(body), { timeout: 5_000 })
      .toBeGreaterThan(100);
    await expect(counts).toBeVisible();
    await expect(bucket(1)).toHaveText("◐ 1");
  });

  test("the running task keeps its own row, folded and open", async ({
    page,
  }) => {
    await openPlannedChat(page, TODOS_TWO_DONE);

    const { header, body } = panel(page);
    const now = page.getByTestId("todo-now");

    // Under the header, above the list, and outside the part that folds — the
    // row has to survive the fold, which it cannot do from inside the body.
    await expect(now).toBeVisible();
    expect(await height(body)).toBeLessThan(20);
    const nowBox = (await now.boundingBox())!;
    const headerBox = (await header.boundingBox())!;
    expect(nowBox.y).toBeGreaterThanOrEqual(headerBox.y + headerBox.height);
    await expect(body.locator('[data-testid="todo-now"]')).toHaveCount(0);

    // One running task, so the row is that one name — and it stays put.
    await expect(now).toContainText("Cross-check the tables");
    const rail = page.getByTestId("todo-now-rail");
    await expect(rail.locator("span")).toHaveCount(1);
    const restingOffset = await rail.getAttribute("style");
    await page.waitForTimeout(3000);
    expect(await rail.getAttribute("style")).toBe(restingOffset);

    // Open, the row is still there and the list is what grew.
    await header.click();
    await expect
      .poll(() => height(body), { timeout: 5_000 })
      .toBeGreaterThan(100);
    await expect(now).toBeVisible();
    await expect(now).toContainText("Cross-check the tables");
  });

  test("no running task means no running-task row", async ({ page }) => {
    await openPlannedChat(
      page,
      TODOS.map((todo) =>
        todo.status === "in_progress"
          ? { ...todo, status: "pending" as const }
          : todo,
      ),
    );

    await expect(page.getByTestId("mobile-todo-list")).toBeVisible();
    // Not an empty row, not a leftover gap: the header's next sibling is the
    // body, and the counts are still counted.
    await expect(page.getByTestId("todo-now")).toHaveCount(0);
    await expect(page.getByTestId("todo-counts")).toBeVisible();
    expect(
      await page.evaluate(
        () =>
          document.querySelector(".mobile-todo-panel > header")
            ?.nextElementSibling?.tagName ?? null,
      ),
    ).toBe("MAIN");
    expect(await height(panel(page).body)).toBeLessThan(20);
  });

  test("takes turns between several running tasks", async ({ page }) => {
    // A name is held for 10s (prototype ⑨), so a full round of two — out and
    // back — needs more than the suite's default 30s budget.
    test.setTimeout(90_000);
    await openPlannedChat(page, TODOS_TWO_RUNNING);

    const rail = page.getByTestId("todo-now-rail");
    const { body } = panel(page);

    // A block per running task, plus a copy of the first for the loop to land
    // on. Folded, which is where the row earns its keep.
    await expect(rail.locator("span")).toHaveCount(RUNNING.length + 1);
    expect(await height(body)).toBeLessThan(20);

    // The name in the window — geometry, in a real browser — moves on by
    // itself, and comes back to the first one without ever scrolling back
    // through the names.
    await expect
      .poll(() => visibleName(page), { timeout: 25_000 })
      .toBe(RUNNING[0]);
    await expect
      .poll(() => visibleName(page), { timeout: 25_000 })
      .toBe(RUNNING[1]);
    await expect
      .poll(() => visibleName(page), { timeout: 25_000 })
      .toBe(RUNNING[0]);
  });

  test("does not turn over when the reader asks for less motion", async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openPlannedChat(page, TODOS_TWO_RUNNING);

    const rail = page.getByTestId("todo-now-rail");

    // No copy of the first name to turn over onto, and the first running task
    // is what the row reads — no animation required to get it there.
    await expect(rail.locator("span")).toHaveCount(RUNNING.length);
    await expect(page.getByTestId("todo-now")).toContainText(RUNNING[0]);

    const restingOffset = await rail.getAttribute("style");
    await page.waitForTimeout(6000);
    expect(await rail.getAttribute("style")).toBe(restingOffset);
    expect(await visibleName(page)).toBe(RUNNING[0]);
  });

  test("the header is the toggle: 44px tall, opens and closes the list", async ({
    page,
  }) => {
    await openPlannedChat(page, TODOS);

    const { header, body } = panel(page);
    const headerBox = (await header.boundingBox())!;
    expect(headerBox.height).toBeGreaterThanOrEqual(44);
    expect(headerBox.width).toBeGreaterThanOrEqual(44);

    await header.click();
    await expect
      .poll(() => height(body), { timeout: 5_000 })
      .toBeGreaterThan(100);

    await header.click();
    await expect.poll(() => height(body), { timeout: 5_000 }).toBeLessThan(20);
  });

  test("a thread with no todos renders no panel and no gap", async ({
    page,
  }) => {
    await openPlannedChat(page, []);

    await expect(page.getByTestId("mobile-todo-list")).toHaveCount(0);
    // Not even the shared component's own header, and no leftover spacer: the
    // composer starts exactly where the transcript ends.
    await expect(page.locator(".mobile-todo-panel")).toHaveCount(0);
    const gap = await page.evaluate(() => {
      const composer = document.querySelector<HTMLElement>(
        '[data-testid="mobile-composer"]',
      )!;
      const previous = composer.previousElementSibling as HTMLElement;
      return (
        composer.getBoundingClientRect().top -
        previous.getBoundingClientRect().bottom
      );
    });
    expect(gap).toBe(0);
  });

  test("stays visible with plan mode off, like the desktop", async ({
    page,
  }) => {
    // No `/api/models` override: the mock returns no models, so the composer
    // resolves the unpinned mode to Flash, and plan mode is off. The panel
    // describes what the helper agent last wrote, so it must not be gated on
    // the toggle being on right now.
    await openPlannedChat(page, TODOS);

    await page.getByTestId("mobile-composer-plus").click();
    await page.getByTestId("mobile-composer-sheet-plan").click();
    await expect(page.getByTestId("mobile-composer-plan-off")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByTestId("mobile-composer-plan-on")).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    // Close the sheet again: the panel must be there with plan mode off and
    // nothing of the composer in the way.
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("mobile-composer-sheet")).toBeHidden();
    await expect(page.getByTestId("mobile-todo-list")).toBeVisible();
  });

  test("reads a total for a finished task, so far for a running one, nothing for a waiting one", async ({
    page,
  }) => {
    await openPlannedChat(page, TODOS_WITH_TIME);

    // Readable folded and open; asserted open, where the rows are laid out.
    const { header, body } = panel(page);
    await header.click();
    await expect
      .poll(() => height(body), { timeout: 5_000 })
      .toBeGreaterThan(100);

    // `12m` for the finished pair, `3m` for the running start, and nothing at
    // all on the three that are still waiting.
    expect(await rowDurations(page)).toEqual(["12m", "3m", null, null, null]);

    // A waiting row is given no node to keep the column open, not an empty
    // one: its text is its name and nothing else.
    await expect(body.locator('[data-testid="todo-duration"]')).toHaveCount(2);
    await expect(
      body.locator("li", { hasText: "Write the summary" }),
    ).toHaveText("Write the summary");
  });

  test("counts a running task up without a reload", async ({ page }) => {
    await openPlannedChat(page, [
      {
        content: "Started a moment ago",
        status: "in_progress",
        started_at: ago(40_000),
      },
      { content: "Still waiting", status: "pending" },
    ]);

    // Seconds-grained, so the growth is observable in a couple of ticks
    // rather than after the next minute rolls over.
    const reading = async () =>
      Number(((await rowDurations(page))[0] ?? "").replace("s", ""));
    const first = await reading();
    expect(first).toBeGreaterThanOrEqual(40);

    await expect.poll(reading, { timeout: 10_000 }).toBeGreaterThan(first);
  });

  test("the running-task row carries the shown task's own reading", async ({
    page,
  }) => {
    test.setTimeout(60_000);
    await openPlannedChat(page, TODOS_TWO_RUNNING_WITH_TIME);

    // Read the name and the number together, as one string: the pair only
    // matches while they agree, so a number left behind on the previous task
    // can never satisfy it. That is the bug this test exists for — `5m` beside
    // the second task's name would never make `Write the summary 1m`.
    const shown = async () =>
      `${await visibleName(page)} ${await nowDuration(page)}`;

    await expect.poll(shown, { timeout: 25_000 }).toBe(`${RUNNING[0]} 5m`);
    await expect.poll(shown, { timeout: 25_000 }).toBe(`${RUNNING[1]} 1m`);

    // The same two numbers are the list's own, one row each.
    expect(await rowDurations(page)).toEqual(["5m", "1m", null, null, null]);
  });

  test("shows no time at all for a thread written before the stamps existed", async ({
    page,
  }) => {
    await openPlannedChat(page, TODOS_WITHOUT_TIME);

    // Old threads have neither stamp. Nothing is drawn — and nothing draws
    // `NaN` or `Invalid Date` in their place.
    await expect(page.getByTestId("mobile-todo-list")).toBeVisible();
    expect(await rowDurations(page)).toEqual([null, null, null, null, null]);
    await expect(page.getByTestId("todo-now-duration")).toHaveCount(0);

    const text = await page.locator(".mobile-todo-panel").innerText();
    expect(text).not.toContain("NaN");
    expect(text).not.toContain("Invalid");
  });

  test("the time column does not wrap or push the row sideways", async ({
    page,
  }) => {
    await openPlannedChat(page, TODOS_WITH_TIME);

    const { header, body } = panel(page);
    await header.click();
    await expect
      .poll(() => height(body), { timeout: 5_000 })
      .toBeGreaterThan(100);

    const times = page.locator(
      '.mobile-todo-panel [data-testid="todo-duration"]',
    );
    await expect(times).toHaveCount(2);

    for (const width of [390, 360]) {
      await page.setViewportSize({ width, height: 780 });
      expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);

      for (const time of await times.all()) {
        // One line, unbroken: the name beside it gives way, the reading does
        // not.
        expect(
          await time.evaluate((element) => element.getClientRects().length),
        ).toBe(1);
        const box = (await time.boundingBox())!;
        const row = (await time.locator("xpath=ancestor::li").boundingBox())!;
        expect(box.x + box.width).toBeLessThanOrEqual(row.x + row.width + 1);
      }
    }

    // `tabular-nums`, so the next tick cannot shuffle the names along, and the
    // running reading is the brighter of the two — it is the one still moving.
    expect(
      await times
        .first()
        .evaluate((element) => getComputedStyle(element).fontVariantNumeric),
    ).toContain("tabular-nums");
    const [finished, running] = await times.evaluateAll((elements) =>
      elements.map((element) => getComputedStyle(element).color),
    );
    expect(running).not.toBe(finished);
  });

  test("does not scroll sideways at 390px or 360px, folded or open", async ({
    page,
  }) => {
    await openPlannedChat(page, TODOS);

    const { header, body } = panel(page);
    const composer = page.getByTestId("mobile-composer");

    for (const width of [390, 360]) {
      await page.setViewportSize({ width, height: 780 });

      // The panel survives a rotation: the fold state is component state, so
      // it is put back explicitly rather than assumed.
      if ((await height(body)) > 20) {
        await header.click();
      }
      await expect
        .poll(() => height(body), { timeout: 5_000 })
        .toBeLessThan(20);
      expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);

      await header.click();
      await expect
        .poll(() => height(body), { timeout: 5_000 })
        .toBeGreaterThan(100);
      expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);

      // The open panel takes its height from the transcript, never from the
      // composer: the input stays on screen and fully inside the viewport.
      const composerBox = (await composer.boundingBox())!;
      expect(composerBox.y).toBeGreaterThan(0);
      expect(composerBox.y + composerBox.height).toBeLessThanOrEqual(780);
    }
  });
});
