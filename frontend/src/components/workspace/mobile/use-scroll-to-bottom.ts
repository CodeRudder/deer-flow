"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";

/**
 * Distance from the bottom (px) still counted as "at the bottom". Matches the
 * order of the shared `ConversationScrollButton`'s own tolerance so the button
 * does not flicker while a reply streams in.
 */
export const AT_BOTTOM_TOLERANCE_PX = 16;

/**
 * The element that actually scrolls the transcript.
 *
 * `MessageList` mounts `Conversation` from `ai-elements`, which is
 * `use-stick-to-bottom`'s root: the root carries `role="log"` and the library
 * attaches its scroll ref — and its `overflow: auto` — to that root's first
 * child (`StickToBottom.Content`). The list owns that element and takes no
 * children, so the mobile page cannot mount `ConversationScrollButton`
 * (it needs the stick-to-bottom context) and reads the scroll node instead of
 * editing the shared component.
 *
 * Exported for the unit test.
 */
export function findTranscriptScroller(
  root: HTMLElement | null | undefined,
): HTMLElement | null {
  const log = root?.querySelector('[role="log"]');
  const scroller = log?.firstElementChild;
  return scroller instanceof HTMLElement ? scroller : null;
}

/**
 * Floating "back to bottom" affordance for the mobile chat page.
 *
 * `resetKey` (the thread id) re-binds the listener when the page swaps
 * threads, because the transcript element is replaced on that route change.
 */
export function useScrollToBottom(
  rootRef: RefObject<HTMLElement | null>,
  resetKey: string,
) {
  const [scroller, setScroller] = useState<HTMLElement | null>(null);
  const [isAtBottom, setIsAtBottom] = useState(true);

  useEffect(() => {
    // One frame after render: `MessageList` may have replaced the transcript
    // skeleton with the real list during this commit.
    const raf = requestAnimationFrame(() => {
      setScroller(findTranscriptScroller(rootRef.current));
    });
    return () => cancelAnimationFrame(raf);
  }, [resetKey, rootRef]);

  useEffect(() => {
    if (!scroller) {
      return;
    }
    const update = () => {
      const distance =
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      setIsAtBottom(distance <= AT_BOTTOM_TOLERANCE_PX);
    };
    update();
    scroller.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(scroller);
    return () => {
      scroller.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [scroller]);

  const scrollToBottom = useCallback(() => {
    if (!scroller) {
      return;
    }
    scroller.scrollTo({
      top: scroller.scrollHeight,
      behavior: "smooth",
    });
  }, [scroller]);

  return { isAtBottom, scrollToBottom };
}
