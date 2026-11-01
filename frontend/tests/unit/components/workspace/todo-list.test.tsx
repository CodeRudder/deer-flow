import {
  afterEach,
  beforeEach,
  describe,
  expect,
  rs,
  test,
} from "@rstest/core";
import { act, cleanup, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { TodoList } from "@/components/workspace/todo-list";

/**
 * `TodoList`'s header, and the progress and elapsed-time readings the phone
 * adds to it (T28, T29).
 *
 * The component is shared with the desktop, so the contract under test is
 * opt-in: `showProgress` and `showDurations` are additions, never changes. The
 * first tests pin that by comparing the rendered header and rows against the
 * markup the component produced *before* each prop existed — captured
 * verbatim, so "the desktop is untouched" is a byte comparison rather than a
 * claim. Everything after them covers the phone side: the three counts, the
 * running-task row under them, the rotation between several running tasks (and
 * the elapsed time travelling with it), and the fact that folding the list
 * away takes none of it off screen.
 */

/**
 * The header as it rendered before `showProgress` existed — every class, every
 * lucide path, both divs. Regenerate only if the shared panel is *meant* to
 * change on the desktop; a diff here on an unrelated change is the regression
 * this test is for.
 */
const PRE_CHANGE_HEADER =
  '<header class="bg-accent flex min-h-8 shrink-0 cursor-pointer items-center justify-between px-4 text-sm transition-all duration-300 ease-out"><div class="text-muted-foreground"><div class="flex items-center justify-center gap-2"><svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-list-todo size-4" aria-hidden="true"><path d="M13 5h8"></path><path d="M13 12h8"></path><path d="M13 19h8"></path><path d="m3 17 2 2 4-4"></path><rect x="3" y="4" width="6" height="6" rx="1"></rect></svg><div>To-dos</div></div></div><div><svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-chevron-up text-muted-foreground size-4 transition-transform duration-300 ease-out" aria-hidden="true"><path d="m18 15-6-6-6 6"></path></svg></div></header>';

/**
 * One row per status, as they rendered before `showDurations` existed: a
 * completed row, a running row (the two the time column is added to) and a
 * pending one (the one that must be left alone entirely). Regenerate only if
 * the shared panel is *meant* to change on the desktop; a diff here on an
 * unrelated change is the regression this test is for.
 */
const PRE_CHANGE_COMPLETED_ROW =
  '<li class="group hover:bg-muted flex flex-col gap-1 rounded-md px-3 py-1 text-sm transition-colors"><div class="flex items-center gap-2"><span class="mt-0.5 inline-block size-2.5 rounded-full border border-muted-foreground/20 bg-muted-foreground/10"></span><span class="line-clamp-1 grow break-words text-muted-foreground/50 line-through">Draft the outline</span></div></li>';

const PRE_CHANGE_RUNNING_ROW =
  '<li class="group hover:bg-muted flex flex-col gap-1 rounded-md px-3 py-1 text-sm transition-colors"><div class="flex items-center gap-2"><span class="mt-0.5 inline-block size-2.5 rounded-full border border-muted-foreground/50 bg-primary/70"></span><span class="line-clamp-1 grow break-words text-primary/70">Write the summary</span></div></li>';

const PRE_CHANGE_PENDING_ROW =
  '<li class="group hover:bg-muted flex flex-col gap-1 rounded-md px-3 py-1 text-sm transition-colors"><div class="flex items-center gap-2"><span class="mt-0.5 inline-block size-2.5 rounded-full border border-muted-foreground/50"></span><span class="line-clamp-1 grow break-words text-muted-foreground">Send it for review</span></div></li>';

/** Two done, one running, two waiting — the three buckets at once. */
const TODOS = [
  { content: "Draft the outline", status: "completed" as const },
  { content: "Collect the numbers", status: "completed" as const },
  { content: "Write the summary", status: "in_progress" as const },
  { content: "Send it for review", status: "pending" as const },
  { content: "File the follow-ups", status: "pending" as const },
];

/**
 * jsdom ships no `matchMedia` at all, and the running-task row asks it whether
 * the reader wants animation. The default stub says yes (the common case);
 * tests that want the answer to be no pass their own.
 */
function stubMatchMedia(matches: boolean) {
  rs.stubGlobal(
    "matchMedia",
    rs.fn((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: rs.fn(),
      removeEventListener: rs.fn(),
      addListener: rs.fn(),
      removeListener: rs.fn(),
      dispatchEvent: rs.fn(),
    })),
  );
}

// Stubbed for every test, not just the ones that care: the row asks the
// question as soon as it mounts, and jsdom has no `matchMedia` to answer with.
beforeEach(() => {
  stubMatchMedia(false);
});

afterEach(() => {
  cleanup();
  rs.useRealTimers();
});

function renderPanel(props: Partial<Parameters<typeof TodoList>[0]> = {}) {
  const view = render(<TodoList todos={TODOS} {...props} />);
  const root = view.container.querySelector<HTMLElement>("div")!;
  const header = view.container.querySelector<HTMLElement>("header")!;
  const main = view.container.querySelector<HTMLElement>("main")!;
  return { ...view, root, header, main };
}

/** The rows, in order. */
function rows(container: HTMLElement) {
  return [...container.querySelectorAll("li")];
}

/** The time column of each row, or `null` where the row draws none. */
function durations(container: HTMLElement) {
  return rows(container).map(
    (row) =>
      row.querySelector<HTMLElement>('[data-testid="todo-duration"]')
        ?.textContent ?? null,
  );
}

/** The three counts, as the strings they render. */
function counts(container: HTMLElement) {
  const group = container.querySelector<HTMLElement>(
    '[data-testid="todo-counts"]',
  );
  return group
    ? [...group.children].map((child) => child.textContent ?? "")
    : null;
}

/** The running-task row, and the rail of names and elapsed time inside it. */
function nowRow(container: HTMLElement) {
  const row = container.querySelector<HTMLElement>('[data-testid="todo-now"]');
  const rail = container.querySelector<HTMLElement>(
    '[data-testid="todo-now-rail"]',
  );
  return {
    row,
    rail,
    names: rail
      ? [...rail.children].map((child) => child.textContent ?? "")
      : null,
    offset: rail?.style.transform ?? null,
    duration:
      container.querySelector<HTMLElement>('[data-testid="todo-now-duration"]')
        ?.textContent ?? null,
  };
}

/** Advances the rotation, letting React settle around it. */
function tick(ms: number) {
  act(() => {
    rs.advanceTimersByTime(ms);
  });
}

describe("TodoList header", () => {
  test("renders byte-for-byte the header it rendered before showProgress", () => {
    // No prop, exactly like all three desktop call sites.
    const { header } = renderPanel();

    expect(header.outerHTML).toBe(PRE_CHANGE_HEADER);

    // The same claim in the terms the golden could hide: one text run, the
    // label alone, two children, and nothing extra to the right of the
    // chevron's wrapper.
    expect(header.textContent).toBe("To-dos");
    expect(header.children).toHaveLength(2);
    expect(header.querySelector('[data-testid="todo-counts"]')).toBeNull();
  });

  test("keeps the header's shape when the list is open", async () => {
    const { header } = renderPanel();

    // Expanded, the only difference from the golden is the chevron's own
    // `rotate-180` — a class this change does not touch.
    await userEvent.click(header);

    expect(counts(header.parentElement!)).toBeNull();
    expect(header.textContent).toBe("To-dos");
    expect(header.children).toHaveLength(2);
    expect(header.querySelectorAll("svg")[1]?.getAttribute("class")).toContain(
      "rotate-180",
    );
  });

  test("renders byte-for-byte the rows it rendered before showDurations", () => {
    // No props, exactly like all three desktop call sites. The time column is
    // the only thing this change adds to a row, so the rows are where "the
    // desktop is untouched" has to be proved — including the two that gain
    // nothing (a pending row draws no column at all, not an empty slot).
    const { root } = renderPanel();

    expect(rows(root)).toHaveLength(TODOS.length);
    // One per status: the completed and running rows are the ones the column
    // would land on, the pending one the one it must skip.
    expect(rows(root)[0]?.outerHTML).toBe(PRE_CHANGE_COMPLETED_ROW);
    expect(rows(root)[2]?.outerHTML).toBe(PRE_CHANGE_RUNNING_ROW);
    expect(rows(root)[3]?.outerHTML).toBe(PRE_CHANGE_PENDING_ROW);

    // The same claim in the terms a golden could hide: no time nodes at all.
    expect(root.querySelectorAll('[data-testid="todo-duration"]')).toHaveLength(
      0,
    );
  });

  test("showProgress counts done, running and waiting", () => {
    const { header } = renderPanel({ showProgress: true });

    // `✓ 2 / ◐ 1 / ○ 2` for the five above, in that order.
    expect(counts(header.parentElement!)).toEqual(["✓ 2", "◐ 1", "○ 2"]);

    // Right-aligned, immediately before the chevron: the counts and the
    // chevron share one flex row, which the header's own `justify-between`
    // pushes to the trailing edge.
    const group = header.querySelector('[data-testid="todo-counts"]');
    const chevron = group?.nextElementSibling;
    expect(chevron?.tagName.toLowerCase()).toBe("svg");
    expect(group?.parentElement).toBe(chevron?.parentElement);
    expect(group?.parentElement?.className).toContain("flex");
    expect(header.className).toContain("justify-between");

    // The label is untouched: the counts are added beside it, not in place of
    // the header's existing text.
    expect(header.textContent).toBe("To-dos" + "✓ 2◐ 1○ 2");
  });

  test("counts a todo with no status as waiting", () => {
    // The three buckets always add up to the list, so a todo the helper agent
    // published without a status is "not started" rather than invisible.
    const { header } = renderPanel({
      todos: [{ content: "No status yet" }, ...TODOS],
      showProgress: true,
    });

    expect(counts(header.parentElement!)).toEqual(["✓ 2", "◐ 1", "○ 3"]);
  });

  test("renders no 0/0/0 for an empty list", () => {
    const { header, root } = renderPanel({ todos: [], showProgress: true });

    expect(counts(root)).toBeNull();
    expect(nowRow(root).row).toBeNull();
    expect(header.textContent).toBe("To-dos");
  });

  test("the counts are tabular so the digits do not jump", () => {
    const { header } = renderPanel({ showProgress: true });

    // `font-variant-numeric: tabular-nums` — asserted as the class because
    // jsdom applies no stylesheet.
    const group = header.querySelector('[data-testid="todo-counts"]');
    expect(group?.className).toContain("tabular-nums");
  });
});

describe("TodoList running-task row", () => {
  test("shows the running task under the header", () => {
    const { root } = renderPanel({ showProgress: true });

    const { row, names } = nowRow(root);
    expect(row?.textContent).toBe("Write the summary");
    expect(names).toEqual(["Write the summary"]);

    // A row of its own between the header and the list, and inside the accent
    // band the header sits in.
    expect(row?.previousElementSibling?.tagName.toLowerCase()).toBe("header");
    expect(root.children).toHaveLength(3);
  });

  test("draws nothing when no task is running", () => {
    const { root } = renderPanel({
      todos: TODOS.filter((todo) => todo.status !== "in_progress"),
      showProgress: true,
    });

    // No row, no spacer, nothing: the panel is the header and the body.
    expect(nowRow(root).row).toBeNull();
    expect(root.children).toHaveLength(2);
  });

  test("holds a single running task still", async () => {
    rs.useFakeTimers();
    const { root } = renderPanel({ showProgress: true });

    const before = nowRow(root).offset;
    tick(30_000);

    // No copy of the name, no movement: one task has nothing to take turns
    // with.
    expect(nowRow(root).names).toEqual(["Write the summary"]);
    expect(nowRow(root).offset).toBe(before);
  });

  test("takes turns between several running tasks every 10s", async () => {
    rs.useFakeTimers();
    const { root } = renderPanel({
      todos: [
        { content: "Collect the numbers", status: "in_progress" },
        { content: "Cross-check the tables", status: "in_progress" },
      ],
      showProgress: true,
    });

    // One block per running task, plus a copy of the first for the loop to
    // land on and be wound back from.
    expect(nowRow(root).names).toEqual([
      "Collect the numbers",
      "Cross-check the tables",
      "Collect the numbers",
    ]);
    const first = nowRow(root).offset;

    // Prototype ⑨: 9s standing still + 1s sliding, so a name is held for 10s
    // and the round takes 20s. Nothing has moved 9s in, and the slide has
    // started by 10s — the interval is pinned to the second.
    tick(9000);
    expect(nowRow(root).offset).toBe(first);

    tick(1000);
    const second = nowRow(root).offset;
    expect(second).not.toBe(first);

    // Onto the copy…
    tick(10_000);
    expect(nowRow(root).offset).not.toBe(first);

    // …and, once the slide has settled, back to the top — the loop restarts
    // without ever scrolling backwards through the names.
    tick(1000);
    expect(nowRow(root).offset).toBe(first);

    // And it keeps going round.
    tick(10_000);
    expect(nowRow(root).offset).toBe(second);
  });

  test("keeps the copy of the first name out of the reading order", async () => {
    rs.useFakeTimers();
    const { root } = renderPanel({
      todos: [
        { content: "Collect the numbers", status: "in_progress" },
        { content: "Cross-check the tables", status: "in_progress" },
      ],
      showProgress: true,
    });

    // The rail holds three blocks but only two distinct names; the copy is
    // marked `aria-hidden` so it is not read out twice.
    const blocks = root.querySelectorAll(
      '[data-testid="todo-now-rail"] > span',
    );
    expect(blocks).toHaveLength(3);
    expect(
      [...blocks].map((block) => block.getAttribute("aria-hidden")),
    ).toEqual([null, null, "true"]);
  });

  test("does not turn over at all under prefers-reduced-motion", () => {
    rs.useFakeTimers();
    stubMatchMedia(true);
    const { root } = renderPanel({
      todos: [
        { content: "Collect the numbers", status: "in_progress" },
        { content: "Cross-check the tables", status: "in_progress" },
      ],
      showProgress: true,
    });

    // No turn-over, so no copy of the first name at the end of the rail to
    // turn over onto — the other names are still in the rail, clipped by the
    // window, but nothing moves them into view. The first name is what the
    // row reads, with no animation needed to get it there.
    expect(nowRow(root).names).toEqual([
      "Collect the numbers",
      "Cross-check the tables",
    ]);
    expect(nowRow(root).offset).toBe("translateY(-0rem)");
    tick(30_000);
    expect(nowRow(root).offset).toBe("translateY(-0rem)");
  });

  test("the rail is a block, so it can actually move", () => {
    const { root } = renderPanel({ showProgress: true });

    // A `transform` on a non-replaced inline element does nothing — silently.
    expect(nowRow(root).rail?.className).toContain("block");
    expect(nowRow(root).rail?.className).toContain("transition-transform");
    // The slide is the prototype's 1s, and this class is what has to agree
    // with it: the wind-back is timed off the same constant.
    expect(nowRow(root).rail?.className).toContain("duration-1000");
    // And the reduced-motion path turns the slide off as well, so a reader
    // whose system says no gets no movement even if something moves it.
    expect(nowRow(root).rail?.className).toContain(
      "motion-reduce:transition-none",
    );
  });
});

const MINUTE = 60_000;

/**
 * Todos carrying the stamps the backend writes, placed relative to the frozen
 * clock — so the readings under test are exact strings (`12m`) rather than
 * "about a minute".
 */
function stampedTodos() {
  return [
    {
      content: "Draft the outline",
      status: "completed" as const,
      started_at: new Date(Date.now() - 12 * MINUTE).toISOString(),
      completed_at: new Date(Date.now()).toISOString(),
    },
    {
      content: "Write the summary",
      status: "in_progress" as const,
      started_at: new Date(Date.now() - 3 * MINUTE).toISOString(),
    },
    { content: "Send it for review", status: "pending" as const },
  ];
}

describe("TodoList elapsed times", () => {
  test("reads a finished todo's total and a running one's so far", () => {
    rs.useFakeTimers();
    const { root } = renderPanel({
      todos: stampedTodos(),
      showDurations: true,
    });

    // The finished row's two ends are fixed; the running row is measured
    // against the clock. The pending row has nothing to say.
    expect(durations(root)).toEqual(["12m", "3m", null]);
  });

  test("grows the running reading as the clock does", () => {
    rs.useFakeTimers();
    const { root } = renderPanel({
      todos: stampedTodos(),
      showDurations: true,
    });
    expect(durations(root)).toEqual(["12m", "3m", null]);

    tick(MINUTE);

    // The running row counted on; the finished row did not move — it is a
    // total, not an elapsed time.
    expect(durations(root)).toEqual(["12m", "4m", null]);
  });

  test("says nothing for a thread written before the stamps existed", () => {
    rs.useFakeTimers();
    // `TODOS` is exactly that shape: no `started_at`, no `completed_at`.
    const { root } = renderPanel({ todos: TODOS, showDurations: true });

    // No `NaN`, no `Invalid Date`, no negative, no crash — and a column of
    // nothing rather than a column of placeholders.
    expect(durations(root)).toEqual([null, null, null, null, null]);
    expect(rows(root).map((row) => row.textContent)).toEqual(
      TODOS.map((todo) => todo.content),
    );
    expect(root.textContent).not.toContain("NaN");
    expect(root.textContent).not.toContain("Invalid");
  });

  test("draws no node at all where there is no reading", () => {
    rs.useFakeTimers();
    const { root } = renderPanel({
      todos: stampedTodos(),
      showDurations: true,
    });

    // Two rows have a reading, one has none: the waiting row is not given an
    // empty span to keep the column open — it is left as it always was.
    expect(root.querySelectorAll('[data-testid="todo-duration"]')).toHaveLength(
      2,
    );
    const waiting = rows(root)[2]!;
    expect(waiting.querySelector('[data-testid="todo-duration"]')).toBeNull();
    expect(waiting.querySelector("div")?.children).toHaveLength(2);
    expect(waiting.textContent).toBe("Send it for review");
  });

  test("the running row and the list row read the same number", () => {
    rs.useFakeTimers();
    const { root } = renderPanel({
      todos: stampedTodos(),
      showProgress: true,
      showDurations: true,
    });

    // One task, one clock, two places on screen — they cannot disagree.
    expect(nowRow(root).duration).toBe("3m");
    expect(durations(root)[1]).toBe("3m");

    tick(MINUTE);

    expect(nowRow(root).duration).toBe("4m");
    expect(durations(root)[1]).toBe("4m");
  });

  test("the elapsed time travels with the rotation", () => {
    rs.useFakeTimers();
    const { root } = renderPanel({
      todos: [
        {
          content: "Collect the numbers",
          status: "in_progress" as const,
          started_at: new Date(Date.now() - 5 * MINUTE).toISOString(),
        },
        {
          content: "Cross-check the tables",
          status: "in_progress" as const,
          started_at: new Date(Date.now() - MINUTE).toISOString(),
        },
      ],
      showProgress: true,
      showDurations: true,
    });

    // The number belongs to the name beside it: turn the rail over and the
    // reading turns over with it. A number that stayed on the first task
    // would still be `5m` here.
    expect(nowRow(root).duration).toBe("5m");

    tick(10_000);

    expect(nowRow(root).duration).toBe("1m");

    // Back round to the first, copy and all.
    tick(10_000);
    tick(1000);
    expect(nowRow(root).duration).toBe("5m");
  });

  test("a lone running task holds still but still counts up", () => {
    rs.useFakeTimers();
    const { root } = renderPanel({
      todos: [
        {
          content: "Only one",
          status: "in_progress" as const,
          started_at: new Date(Date.now() - 2 * MINUTE).toISOString(),
        },
      ],
      showProgress: true,
      showDurations: true,
    });

    // Rotation and the elapsed time are separate features: nothing to take
    // turns with, and the clock runs anyway.
    expect(nowRow(root).names).toEqual(["Only one"]);
    expect(nowRow(root).duration).toBe("2m");
    expect(nowRow(root).offset).toBe("translateY(-0rem)");

    tick(30_000);
    expect(nowRow(root).duration).toBe("2m");
    tick(30_000);
    expect(nowRow(root).duration).toBe("3m");

    // Still not a copy of the name, and still not moving.
    expect(nowRow(root).names).toEqual(["Only one"]);
    expect(nowRow(root).offset).toBe("translateY(-0rem)");
  });

  test("counts up under prefers-reduced-motion, which stops only the turn-over", () => {
    rs.useFakeTimers();
    stubMatchMedia(true);
    const { root } = renderPanel({
      todos: [
        {
          content: "Collect the numbers",
          status: "in_progress" as const,
          started_at: new Date(Date.now() - 3 * MINUTE).toISOString(),
        },
        {
          content: "Cross-check the tables",
          status: "in_progress" as const,
          started_at: new Date(Date.now() - MINUTE).toISOString(),
        },
      ],
      showProgress: true,
      showDurations: true,
    });

    // No copy of the first name, no movement — but the number is the
    // information, and there is no animation in it to suppress.
    expect(nowRow(root).names).toEqual([
      "Collect the numbers",
      "Cross-check the tables",
    ]);
    expect(nowRow(root).duration).toBe("3m");

    tick(MINUTE);

    expect(nowRow(root).offset).toBe("translateY(-0rem)");
    expect(nowRow(root).duration).toBe("4m");
  });

  test("showProgress on its own still draws no time", () => {
    rs.useFakeTimers();
    const { root } = renderPanel({
      todos: stampedTodos(),
      showProgress: true,
    });

    // The two additions are independent: a reader of the counts is not
    // handed a time column, in the header's row or anywhere else.
    expect(root.querySelectorAll('[data-testid="todo-duration"]')).toHaveLength(
      0,
    );
    expect(nowRow(root).duration).toBeNull();
    expect(nowRow(root).row?.textContent).toBe("Write the summary");
  });
});

describe("TodoList folded", () => {
  test("keeps the counts and the running task on screen", async () => {
    const { header, main, root } = renderPanel({ showProgress: true });

    // Folded on arrival, like the desktop: the body is height 0, so the
    // header and the running-task row are all there is — which is the whole
    // point of both living outside the body.
    expect(main.className).toContain("h-0");
    expect(counts(root)).toEqual(["✓ 2", "◐ 1", "○ 2"]);
    expect(nowRow(root).row?.textContent).toBe("Write the summary");
    expect(document.body.contains(nowRow(root).row)).toBe(true);

    // Same after a round trip through the open state.
    await userEvent.click(header);
    expect(main.className).toContain("h-28");
    expect(counts(root)).toEqual(["✓ 2", "◐ 1", "○ 2"]);
    expect(nowRow(root).row?.textContent).toBe("Write the summary");

    await userEvent.click(header);
    expect(main.className).toContain("h-0");
    expect(counts(root)).toEqual(["✓ 2", "◐ 1", "○ 2"]);
    expect(nowRow(root).row?.textContent).toBe("Write the summary");
  });

  test("keeps up with the todos as they change", () => {
    const { root, rerender } = renderPanel({ showProgress: true });
    expect(counts(root)).toEqual(["✓ 2", "◐ 1", "○ 2"]);

    rerender(
      <TodoList
        todos={[
          { content: "Draft the outline", status: "completed" },
          { content: "Report the numbers", status: "in_progress" },
        ]}
        showProgress
      />,
    );

    expect(counts(root)).toEqual(["✓ 1", "◐ 1", "○ 0"]);
    expect(nowRow(root).row?.textContent).toBe("Report the numbers");
  });
});
