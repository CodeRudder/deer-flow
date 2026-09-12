"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Pin state for the mobile thread list.
 *
 * `AgentThread` has no pin field and the backend has no pin endpoint, and T4
 * forbids touching `core/**` (where the thread API and its caches live), so the
 * preference is stored per device in `localStorage`. That is the same contract
 * the rest of the app already uses for device-local UI state (locale, sidebar
 * state, local settings) — it just does not sync across devices, which the
 * prototype's "长按可置顶" does not promise either.
 *
 * Reads happen in an effect, never during render: the page is server-rendered
 * first, where `localStorage` does not exist, and a render-time read would
 * either crash on the server or hydrate a different first frame than the one
 * that was sent.
 */
const PINNED_THREADS_STORAGE_KEY = "deerflow.mobile.pinnedThreads";

function readPinnedThreads(): string[] {
  try {
    const raw = window.localStorage.getItem(PINNED_THREADS_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter((id): id is string => typeof id === "string");
  } catch {
    // Private-mode Safari throws on localStorage access, and a corrupted value
    // must not take the list down — an unpinned list is the safe fallback.
    return [];
  }
}

function writePinnedThreads(ids: string[]) {
  try {
    window.localStorage.setItem(
      PINNED_THREADS_STORAGE_KEY,
      JSON.stringify(ids),
    );
  } catch {
    // Storage full or unavailable: the in-memory state still reflects the tap,
    // it just will not survive a reload.
  }
}

export function usePinnedThreads() {
  const [pinnedThreadIds, setPinnedThreadIds] = useState<string[]>([]);

  useEffect(() => {
    setPinnedThreadIds(readPinnedThreads());
  }, []);

  const isPinned = useCallback(
    (threadId: string) => pinnedThreadIds.includes(threadId),
    [pinnedThreadIds],
  );

  const togglePin = useCallback((threadId: string) => {
    setPinnedThreadIds((current) => {
      const next = current.includes(threadId)
        ? current.filter((id) => id !== threadId)
        : [threadId, ...current];
      writePinnedThreads(next);
      return next;
    });
  }, []);

  return { pinnedThreadIds, isPinned, togglePin };
}
