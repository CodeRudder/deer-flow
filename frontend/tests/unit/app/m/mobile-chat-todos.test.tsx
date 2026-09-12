import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MobileChatPage from "@/app/m/(app)/(fullbleed)/workspace/chats/[thread_id]/page";

/**
 * C11 on the phone: the plan-mode todo panel above the composer.
 *
 * The panel itself is the desktop's `TodoList` (`components/workspace/
 * todo-list.tsx`), which the phone side is not allowed to redesign — so the
 * assertions here are about the three things the mobile page is responsible
 * for, plus the shared component's own collapse contract:
 *
 *   1. the panel is rendered only when the thread has todos, and it is handed
 *      the thread's own list (not a copy or a re-fetch);
 *   2. it sits *above* the composer in the same flex column;
 *   3. the phone-specific styling hooks it needs exist in `chat-surface.css`
 *      (`translate` off, a closed bottom edge, a 44px toggle bar), scoped so
 *      the desktop is untouched.
 *
 * The real geometry — a 390px viewport, the real composer, the real thread
 * stream — is asserted in `tests/e2e/mobile-todos.spec.ts`, which this file
 * cannot stand in for: jsdom applies no stylesheet, so a collapsed panel and
 * an expanded one are indistinguishable by layout. The class on the panel's
 * body is the observable proxy (the shared component writes `h-0` when folded
 * and `h-28` when open).
 *
 * Everything below `useChatPage()` is mocked: the page's own contract is what
 * is under test, and the hook, the transcript and the composer all carry
 * dependencies (React Query, the stream, the prompt-input controller) that
 * belong to their own suites.
 */

const mockTodos = rs.hoisted(() => ({
  current: [] as { content?: string; status?: string }[],
}));

const mockRouter = rs.hoisted(() => ({
  push: rs.fn(),
  replace: rs.fn(),
  prefetch: rs.fn(),
  back: rs.fn(),
}));

const THREAD_ID = "11111111-1111-1111-1111-111111111111";

rs.mock("next/navigation", () => ({
  useParams: () => ({ thread_id: THREAD_ID }),
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => mockRouter,
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    locale: "en-US",
    t: {
      pages: {
        appName: "DeerFlow",
        newChat: "New chat",
        untitled: "Untitled",
      },
      chats: { scrollToBottom: "Scroll to bottom" },
      humanInput: { inputDisabledHint: "Answer the question first" },
    },
  }),
}));

/**
 * `hasTodos` is derived here exactly as `use-chat-page.ts` derives it — the
 * page's gate must follow the thread state, not a flag of its own, and a
 * thread whose `todos` key is missing must read as empty.
 */
rs.mock("@/components/workspace/chats", () => ({
  useChatPage: () => {
    const todos = mockTodos.current;
    return {
      threadId: THREAD_ID,
      isMock: false,
      isNewThread: false,
      thread: {
        error: undefined,
        isLoading: false,
        messages: [],
        values: { todos },
      },
      title: "Plan mode",
      pendingUsageMessages: [],
      backendTokenUsage: undefined,
      tokenUsageEnabled: false,
      tokenUsageInlineMode: "off" as const,
      settings: { context: {} },
      setSettings: rs.fn(),
      localSettings: { tokenUsage: {} },
      setLocalSettings: rs.fn(),
      isHistoryLoading: false,
      hasMoreHistory: false,
      loadMoreHistory: rs.fn(),
      hasOpenHumanInputCard: false,
      handleSubmitHumanInput: rs.fn(),
      handleSubmit: rs.fn(),
      handleStop: rs.fn(),
      handleRegenerate: rs.fn(),
      hasTodos: (todos?.length ?? 0) > 0,
      isDemoMode: false,
      isInputDisabled: false,
      canRegenerate: false,
    };
  },
}));

/** The transcript is not what this file is about; it drags in streamdown. */
rs.mock("@/components/workspace/messages", () => ({
  MessageList: () => null,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM: 0,
}));

rs.mock("@/components/workspace/mobile/chat-header", () => ({
  MobileChatHeader: () => null,
}));

rs.mock("@/components/workspace/mobile/use-scroll-to-bottom", () => ({
  useScrollToBottom: () => ({ isAtBottom: true, scrollToBottom: rs.fn() }),
}));

/**
 * The composer stands in for the real one, but keeps the only thing the
 * ordering assertion needs: an element of its own to be compared against.
 * The panel must be a sibling *before* it (the page composes the two; the
 * composer knows nothing about todos).
 */
rs.mock("@/components/workspace/mobile/composer", () => ({
  MobileComposer: () => <div data-testid="mobile-composer" />,
}));

// jsdom ships no `matchMedia`, and the page mounts `SidebarProvider`
// (`ui/sidebar`), whose `useIsMobile` reads it for the tablet breakpoint.
rs.stubGlobal(
  "matchMedia",
  rs.fn((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: rs.fn(),
    removeEventListener: rs.fn(),
    addListener: rs.fn(),
    removeListener: rs.fn(),
    dispatchEvent: rs.fn(),
  })),
);

afterEach(() => {
  cleanup();
  rs.clearAllMocks();
  mockTodos.current = [];
});

function renderPage() {
  render(<MobileChatPage />);
  return screen.getByTestId("mobile-composer");
}

/** The shared panel's root — the element `chat-surface.css` hooks onto. */
function panelRoot(): HTMLElement {
  const wrapper = screen.getByTestId("mobile-todo-list");
  const root = wrapper.querySelector<HTMLElement>(".mobile-todo-panel");
  if (!root) {
    throw new Error("the todo panel is missing its .mobile-todo-panel class");
  }
  return root;
}

function panelBody(): HTMLElement {
  return panelRoot().querySelector<HTMLElement>("main")!;
}

function panelHeader(): HTMLElement {
  return panelRoot().querySelector<HTMLElement>("header")!;
}

const TODOS = [
  { content: "Draft the outline", status: "completed" },
  { content: "Collect the numbers", status: "in_progress" },
  { content: "Write the summary", status: "pending" },
];

describe("mobile chat todo panel", () => {
  test("renders the thread's todos above the composer", () => {
    mockTodos.current = TODOS;
    const composer = renderPage();

    const wrapper = screen.getByTestId("mobile-todo-list");
    expect(wrapper).toBeTruthy();

    // Every todo, in the order the thread published them — the count is the
    // transcript's, not a page-local slice. Queried inside the body: the
    // running-task row above it names one of them too.
    const list = within(panelBody());
    for (const todo of TODOS) {
      expect(list.queryAllByText(todo.content)).toHaveLength(1);
    }
    expect(panelRoot().querySelectorAll("li")).toHaveLength(TODOS.length);

    // Above the composer, not inside it, and immediately before it: the
    // column reads transcript container → panel → composer.
    expect(composer.contains(wrapper)).toBe(false);
    const column = Array.from(composer.parentElement?.children ?? []);
    expect(column).toHaveLength(3);
    expect(column[1]).toBe(wrapper);
    expect(column[2]).toBe(composer);

    // Inside the surface those CSS rules are scoped to — without this the
    // panel would render with the desktop's tuck and a 32px toggle.
    expect(wrapper.closest(".mobile-chat-surface")).toBe(
      composer.parentElement,
    );
  });

  test("renders nothing at all when the thread has no todos", () => {
    mockTodos.current = [];
    const composer = renderPage();

    expect(screen.queryByTestId("mobile-todo-list")).toBeNull();
    // Not even the shared panel's own header — no empty shell, and therefore
    // no gap above the composer (the wrapper carries the padding).
    expect(screen.queryByText("To-dos")).toBeNull();
    expect(screen.queryByText("待办")).toBeNull();
    expect(document.querySelector(".mobile-todo-panel")).toBeNull();

    // The column is untouched: the transcript container and the composer,
    // nothing between them.
    expect(composer.parentElement?.children).toHaveLength(2);
  });

  test("shows the progress reading, folded included", () => {
    // T28: the phone is the one caller that asks for the progress reading.
    // The default desktop header has neither node, so their presence here is
    // also proof the page passes `showProgress` down.
    mockTodos.current = TODOS;
    renderPage();

    // One done, one running, one waiting — in the header, immediately before
    // the chevron. The header's own `justify-between` is what makes that the
    // right-hand edge, and the toggle is the entire header, so the counts sit
    // inside the tap target rather than being a control of their own.
    const counts = screen.getByTestId("todo-counts");
    expect([...counts.children].map((child) => child.textContent)).toEqual([
      "✓ 1",
      "◐ 1",
      "○ 1",
    ]);
    const chevron = counts.nextElementSibling;
    expect(panelHeader().contains(counts)).toBe(true);
    expect(chevron?.tagName.toLowerCase()).toBe("svg");
    expect(counts.parentElement).toBe(chevron?.parentElement);

    // The running task has a row of its own, between the header and the list
    // rather than inside the part that folds away.
    const now = screen.getByTestId("todo-now");
    expect(now.textContent).toBe("Collect the numbers");
    expect(now.previousElementSibling).toBe(panelHeader());
    expect(panelBody().contains(now)).toBe(false);

    // Folded — the default a phone lands on — both are still there. That is
    // their whole reason for sitting outside the body.
    expect(panelBody().className).toContain("h-0");
    expect(screen.getByTestId("todo-counts").children).toHaveLength(3);
    expect(screen.getByTestId("todo-now").textContent).toBe(
      "Collect the numbers",
    );
  });

  test("draws no running-task row when nothing is running", () => {
    mockTodos.current = [
      { content: "Draft the outline", status: "completed" },
      { content: "Write the summary", status: "pending" },
    ];
    renderPage();

    expect(screen.queryByTestId("todo-now")).toBeNull();
    // The header is still the counts and the caret, and nothing was left
    // behind between it and the body.
    expect([...screen.getByTestId("todo-counts").children]).toHaveLength(3);
    expect(panelRoot().children).toHaveLength(2);
  });

  test("starts folded and opens on tap", async () => {
    mockTodos.current = TODOS;
    renderPage();

    // Folded: the body is height 0, so the list is in the DOM but not on the
    // screen. jsdom computes no layout, so the class is the observable.
    expect(panelBody().className).toContain("h-0");
    expect(panelBody().className).not.toContain("h-28");

    await userEvent.click(panelHeader());

    expect(panelBody().className).toContain("h-28");
    expect(panelBody().className).not.toContain("h-0");
  });

  test("the toggle bar is 44px tall, not the desktop's 32px", () => {
    mockTodos.current = TODOS;
    renderPage();

    // The height itself comes from `chat-surface.css`; what is asserted here
    // is the pair of class names that CSS depends on, because renaming either
    // one silently drops the touch target back to `min-h-8`.
    const css = readFileSync(
      join(process.cwd(), "src/components/workspace/mobile/chat-surface.css"),
      "utf8",
    );

    expect(panelRoot().className).toContain("mobile-todo-panel");
    expect(css).toContain(".mobile-chat-surface .mobile-todo-panel");
    expect(css).toContain(".mobile-chat-surface .mobile-todo-panel > header");
    expect(css).toMatch(
      /\.mobile-chat-surface \.mobile-todo-panel > header \{[^}]*min-height: 2\.75rem/,
    );
  });
});
