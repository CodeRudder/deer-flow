"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

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
 * `resetKey` (the thread id) re-resolves the transcript when the page swaps
 * threads, because `MessageList` re-mounts around it. It is not the only way
 * the transcript changes, though: see the effect below.
 */
export function useScrollToBottom(
  rootRef: RefObject<HTMLElement | null>,
  resetKey: string,
) {
  const [scroller, setScroller] = useState<HTMLElement | null>(null);
  const [isAtBottom, setIsAtBottom] = useState(true);
  // Mirrors `scroller` so a resolution can be compared against the current one
  // without making the observer re-subscribe on every change.
  const scrollerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) {
      return;
    }

    const sync = () => {
      const next = findTranscriptScroller(root);
      if (next === scrollerRef.current) {
        return;
      }
      scrollerRef.current = next;
      setScroller(next);
    };

    // Resolve now as well as on mutation: this effect also re-runs when the
    // thread id changes, and by then the transcript may already be on screen.
    sync();

    // On a cold load of `/workspace/chats/{id}` — tapping a conversation from
    // the thread list, the normal way in — `MessageList` is still painting
    // `MessageListSkeleton` when this effect runs, so the first resolution
    // finds nothing. The thread id never changes for an existing thread, so
    // there is no second chance for the life of the page: one probe is not
    // enough, and the affordance stays unreachable until the first send
    // happens to change the id. Watch the surface instead — the
    // skeleton→transcript swap, and any later replacement of the scroll node,
    // are childList mutations under it.
    //
    // Cheap during a stream: observer callbacks are coalesced per microtask
    // checkpoint, `characterData` is not observed (so the tokens that stream
    // into a message do not notify at all), and `sync` is an identity compare
    // plus one scoped `querySelector`.
    const observer = new MutationObserver(sync);
    observer.observe(root, { childList: true, subtree: true });

    return () => {
      observer.disconnect();
      scrollerRef.current = null;
    };
  }, [resetKey, rootRef]);

  // Rebinding `scroller` (a replacement transcript, or the skeleton coming
  // back) tears this effect down first: React runs the cleanup below with the
  // previous `scroller` in scope, so the detached node loses its listener and
  // its `ResizeObserver` before the new one is bound.
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
