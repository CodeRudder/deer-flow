import { afterEach, beforeEach, describe, expect, test } from "@rstest/core";
import { act, cleanup, renderHook } from "@testing-library/react";
import { createElement, type ReactNode, type RefObject } from "react";

import {
  AT_BOTTOM_TOLERANCE_PX,
  findTranscriptScroller,
  useScrollToBottom,
} from "@/components/workspace/mobile/use-scroll-to-bottom";

/**
 * The sticky-scroll hook behind the mobile "back to bottom" affordance.
 *
 * The lookup walks a DOM shape owned by an external library, and the binding
 * has to survive the two shapes `MessageList` paints in sequence: the skeleton
 * (no transcript at all) and the real list. The cold-load bug this file
 * pins — the transcript arriving after the hook's first paint — is invisible
 * to a browser test that only ever loads a chat by sending a message, because
 * that path changes the thread id and re-runs the effect.
 */

// jsdom (29.1.1) ships neither `ResizeObserver` nor layout: the hook's
// observer needs a stand-in, and `scrollHeight`/`clientHeight`/`scrollTo`
// have to be supplied by hand. Both recorders exist so the disposal test can
// assert `disconnect()` — a leaked observer per chat navigation is the failure
// mode that makes this worth asserting rather than assuming.
class RecordingResizeObserver {
  static readonly instances: RecordingResizeObserver[] = [];
  observed: Element[] = [];
  disconnectCalls = 0;

  constructor(_callback: ResizeObserverCallback) {
    RecordingResizeObserver.instances.push(this);
  }

  observe(target: Element) {
    this.observed.push(target);
  }

  disconnect() {
    this.disconnectCalls += 1;
  }
}

const NativeMutationObserver = globalThis.MutationObserver;
const NativeResizeObserver = globalThis.ResizeObserver;

class RecordingMutationObserver extends NativeMutationObserver {
  static readonly instances: RecordingMutationObserver[] = [];
  disconnectCalls = 0;

  constructor(callback: MutationCallback) {
    super(callback);
    RecordingMutationObserver.instances.push(this);
  }

  override disconnect() {
    this.disconnectCalls += 1;
    super.disconnect();
  }
}

beforeEach(() => {
  RecordingResizeObserver.instances.length = 0;
  RecordingMutationObserver.instances.length = 0;
  globalThis.ResizeObserver =
    RecordingResizeObserver as unknown as typeof ResizeObserver;
  globalThis.MutationObserver =
    RecordingMutationObserver as unknown as typeof MutationObserver;
});

afterEach(() => {
  cleanup();
  globalThis.ResizeObserver = NativeResizeObserver;
  globalThis.MutationObserver = NativeMutationObserver;
});

describe("findTranscriptScroller", () => {
  /** The shape `MessageList` renders: a `role="log"` root wrapping the scroller. */
  function buildRoot(firstChild: Node | null) {
    const root = document.createElement("div");
    const log = document.createElement("div");
    log.setAttribute("role", "log");
    if (firstChild) {
      log.appendChild(firstChild);
    }
    root.appendChild(log);
    return root;
  }

  test("returns the first element child of the role=log node", () => {
    const scroller = document.createElement("div");
    expect(findTranscriptScroller(buildRoot(scroller))).toBe(scroller);
  });

  test("returns null when the root or the log node is absent", () => {
    expect(findTranscriptScroller(null)).toBeNull();
    expect(findTranscriptScroller(undefined)).toBeNull();
    expect(findTranscriptScroller(document.createElement("div"))).toBeNull();
  });

  test("returns null while the surface still holds the loading skeleton", () => {
    // What the cold-load bug looked like: a surface with children, none of
    // them the transcript.
    const root = document.createElement("div");
    for (let index = 0; index < 3; index += 1) {
      root.appendChild(document.createElement("div"));
    }
    expect(findTranscriptScroller(root)).toBeNull();
  });

  test("returns null for a text-only child, which has no scroll properties", () => {
    expect(
      findTranscriptScroller(buildRoot(document.createTextNode("…"))),
    ).toBeNull();
  });
});

describe("AT_BOTTOM_TOLERANCE_PX", () => {
  test("is tight enough that a real scroll-away still shows the button", () => {
    expect(AT_BOTTOM_TOLERANCE_PX).toBeGreaterThan(0);
    expect(AT_BOTTOM_TOLERANCE_PX).toBeLessThanOrEqual(32);
  });
});

// ---------------------------------------------------------------------------
// Binding
// ---------------------------------------------------------------------------

/** jsdom has no layout, so the numbers the hook reads are defined by hand. */
function applyGeometry(
  scroller: HTMLElement,
  {
    scrollHeight = 1000,
    clientHeight = 500,
    scrollTop = 0,
  }: { scrollHeight?: number; clientHeight?: number; scrollTop?: number } = {},
) {
  Object.defineProperty(scroller, "scrollHeight", {
    value: scrollHeight,
    configurable: true,
  });
  Object.defineProperty(scroller, "clientHeight", {
    value: clientHeight,
    configurable: true,
  });
  scroller.scrollTop = scrollTop;
}

function paintSkeleton(surface: HTMLElement) {
  const skeleton = document.createElement("div");
  skeleton.setAttribute("data-testid", "message-list-skeleton");
  surface.replaceChildren(skeleton);
}

/**
 * `MessageList`'s real DOM: the stick-to-bottom root carries `role="log"` and
 * the scroll node is its first element child. Returns that scroll node.
 */
function paintTranscript(
  surface: HTMLElement,
  geometry?: Parameters<typeof applyGeometry>[1],
) {
  const log = document.createElement("div");
  log.setAttribute("role", "log");
  const scroller = document.createElement("div");
  applyGeometry(scroller, geometry);
  scroller.scrollTo = (() => {
    scroller.scrollTop = scroller.scrollHeight;
  }) as typeof scroller.scrollTo;
  scrollListeners.set(scroller, { added: 0, removed: 0 });
  const add = scroller.addEventListener.bind(scroller);
  const remove = scroller.removeEventListener.bind(scroller);
  scroller.addEventListener = ((type, listener, options) => {
    const counter = scrollListeners.get(scroller);
    if (type === "scroll" && counter) counter.added += 1;
    add(type, listener, options);
  }) as typeof scroller.addEventListener;
  scroller.removeEventListener = ((type, listener, options) => {
    const counter = scrollListeners.get(scroller);
    if (type === "scroll" && counter) counter.removed += 1;
    remove(type, listener, options);
  }) as typeof scroller.removeEventListener;
  log.appendChild(scroller);
  surface.replaceChildren(log);
  return scroller;
}

/**
 * How many `scroll` listeners each painted scroller gained and lost — the
 * evidence for "a replaced transcript is unbound, not kept alive".
 */
const scrollListeners = new WeakMap<
  HTMLElement,
  { added: number; removed: number }
>();

function listenerCounts(scroller: HTMLElement) {
  return scrollListeners.get(scroller) ?? { added: 0, removed: 0 };
}

async function scrollTo(scroller: HTMLElement, top: number) {
  await act(async () => {
    scroller.scrollTop = top;
    scroller.dispatchEvent(new Event("scroll"));
  });
}

/**
 * Mounts the hook the way the mobile page does: `rootRef` is the surface div
 * the page owns for its whole life, while the children underneath it are
 * painted by `MessageList` and get replaced as the thread loads.
 */
function renderSurface({
  initialResetKey = "thread-a",
  mountTranscript = false,
}: { initialResetKey?: string; mountTranscript?: boolean } = {}) {
  const rootRef: RefObject<HTMLDivElement | null> = { current: null };
  const view = renderHook(
    ({ resetKey }: { resetKey: string }) => useScrollToBottom(rootRef, resetKey),
    {
      initialProps: { resetKey: initialResetKey },
      wrapper: ({ children }: { children?: ReactNode }) =>
        createElement(
          "div",
          { ref: rootRef },
          // The transcript as a sibling of the hook's host component: rendered
          // by React during the same commit, so it is already in the DOM when
          // the hook's effect runs.
          mountTranscript
            ? createElement("div", { role: "log" }, createElement("div"))
            : null,
          children,
        ),
    },
  );

  const surface = () => {
    const element = rootRef.current;
    if (!element) {
      throw new Error("surface div was not mounted");
    }
    return element;
  };

  return { surface, view };
}

/** Paints the transcript and lets the hook notice it. */
async function showTranscript(
  surface: HTMLElement,
  geometry?: Parameters<typeof applyGeometry>[1],
): Promise<HTMLElement> {
  let scroller: HTMLElement | null = null;
  await act(async () => {
    scroller = paintTranscript(surface, geometry);
    // The transition is observed through a `MutationObserver`, so the callback
    // lands on a microtask — give it (and the effects it schedules) a turn.
    await Promise.resolve();
    await Promise.resolve();
  });
  if (!scroller) {
    // Reachable only if `act` dropped the assignment above.
    throw new Error("transcript was not painted");
  }
  return scroller as HTMLElement;
}

describe("useScrollToBottom", () => {
  test("stays at the bottom while the surface still shows the skeleton", async () => {
    const { surface, view } = renderSurface();
    await act(async () => {
      paintSkeleton(surface());
    });

    expect(view.result.current.isAtBottom).toBe(true);
  });

  test("binds the transcript that arrives after the first paint", async () => {
    const { surface, view } = renderSurface();
    await act(async () => {
      paintSkeleton(surface());
    });

    // The cold load of `/workspace/chats/{id}`: the skeleton is on screen
    // while the hook resolves, and the transcript replaces it later. Nothing
    // re-runs the effect — the thread id does not change — so the resolution
    // has to be driven by the DOM.
    const scroller = await showTranscript(surface());

    // Flipping to false is what proves the hook found the scroll node: the
    // painted geometry leaves 500px to the bottom, far outside the tolerance,
    // so a hook that never bound would still be reporting `true` here.
    expect(view.result.current.isAtBottom).toBe(false);
    expect(scroller).toBe(surface().querySelector('[role="log"] > *'));
  });

  test("follows the scroll events of the resolved scroller", async () => {
    const { surface, view } = renderSurface();
    await act(async () => {
      paintSkeleton(surface());
    });
    const scroller = await showTranscript(surface());

    await scrollTo(scroller, 500);
    expect(view.result.current.isAtBottom).toBe(true);

    await scrollTo(scroller, 0);
    expect(view.result.current.isAtBottom).toBe(false);
  });

  test("binds a transcript that is already painted when the hook mounts", async () => {
    // The other half of the fix: an effect that only watched for mutations
    // would never resolve a transcript that is already there — the thread id
    // is not going to change, and the shape may not mutate again for a long
    // time. Nothing is mutated in this test after mount.
    const { surface, view } = renderSurface({ mountTranscript: true });
    const scroller = surface().querySelector<HTMLElement>('[role="log"] > *');
    if (!scroller) {
      throw new Error("transcript was not mounted");
    }
    applyGeometry(scroller);

    await scrollTo(scroller, 0);
    expect(view.result.current.isAtBottom).toBe(false);
  });

  test("re-binds to the replacement and stops listening to the detached node", async () => {
    const { surface, view } = renderSurface();
    await act(async () => {
      paintSkeleton(surface());
    });
    const first = await showTranscript(surface());

    // A thread swap (or a history reload) puts the skeleton back and mounts a
    // fresh transcript: a different element, not a re-render of the old one.
    await act(async () => {
      paintSkeleton(surface());
      await Promise.resolve();
    });
    const second = await showTranscript(surface());
    expect(second).not.toBe(first);

    // A scroll on the detached node must not move the state...
    await scrollTo(first, 500);
    expect(view.result.current.isAtBottom).toBe(false);

    // ...because its listener was removed when the effect re-ran. (React runs
    // the previous effect's cleanup with the previous `scroller` in scope, so
    // a replacement does not strand a listener on a node that is no longer on
    // screen — worth pinning, since "the observer keeps a stale node alive" is
    // the obvious way this could rot.)
    expect(listenerCounts(first)).toEqual({ added: 1, removed: 1 });
    expect(listenerCounts(second)).toEqual({ added: 1, removed: 0 });

    // ...while the mounted transcript still drives it.
    await scrollTo(second, 500);
    expect(view.result.current.isAtBottom).toBe(true);
  });

  test("scrolls the resolved scroller, not the surface", async () => {
    const { surface, view } = renderSurface();
    await act(async () => {
      paintSkeleton(surface());
    });
    const scroller = await showTranscript(surface());
    const scrollToCalls: ScrollToOptions[] = [];
    scroller.scrollTo = ((options?: ScrollToOptions) => {
      scrollToCalls.push(options ?? {});
    }) as typeof scroller.scrollTo;

    await act(async () => {
      view.result.current.scrollToBottom();
    });

    expect(scrollToCalls).toEqual([{ top: 1000, behavior: "smooth" }]);
  });

  test("disposes both observers on unmount", async () => {
    const { surface, view } = renderSurface();
    await act(async () => {
      paintSkeleton(surface());
    });
    await showTranscript(surface());

    expect(RecordingResizeObserver.instances.length).toBe(1);
    expect(RecordingMutationObserver.instances.length).toBe(1);

    view.unmount();

    expect(RecordingResizeObserver.instances[0]?.disconnectCalls).toBe(1);
    expect(RecordingMutationObserver.instances[0]?.disconnectCalls).toBe(1);
  });

  test("swaps the observer when the thread id changes instead of stacking them", async () => {
    const { surface, view } = renderSurface({ initialResetKey: "thread-a" });
    await act(async () => {
      paintSkeleton(surface());
    });
    await showTranscript(surface());

    await act(async () => {
      view.rerender({ resetKey: "thread-b" });
    });

    // The navigation disposes the old observer and installs exactly one new
    // one; the scroller itself survived, so its listeners are not churned.
    expect(RecordingMutationObserver.instances.length).toBe(2);
    expect(RecordingMutationObserver.instances[0]?.disconnectCalls).toBe(1);
    expect(RecordingMutationObserver.instances[1]?.disconnectCalls).toBe(0);
    expect(RecordingResizeObserver.instances.length).toBe(1);
  });
});
