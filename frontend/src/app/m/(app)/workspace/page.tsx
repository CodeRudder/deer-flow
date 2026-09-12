"use client";

import { PlusIcon, SearchIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  groupThreads,
  type ThreadGroupId,
} from "@/components/workspace/mobile/thread-groups";
import { ThreadRow } from "@/components/workspace/mobile/thread-row";
import { usePinnedThreads } from "@/components/workspace/mobile/use-pinned-threads";
import { useI18n } from "@/core/i18n/hooks";
import {
  useDeleteThread,
  useInfiniteThreads,
  useRenameThread,
} from "@/core/threads/hooks";
import { titleOfThread } from "@/core/threads/utils";
import { cn } from "@/lib/utils";

/**
 * Mobile thread list — the shell's root screen (prototype ①).
 *
 * The data layer is the desktop sidebar's, unchanged: `useInfiniteThreads()`
 * for the pages, `useRenameThread()` / `useDeleteThread()` for the mutations,
 * and the same `pathOfThread()` builder, so a row links to the *public*
 * `/workspace/chats/<id>` path and the middleware lands it on the mobile chat
 * page. Only the presentation is new.
 *
 * Search copies `app/workspace/chats/page.tsx` rather than inventing a second
 * contract: the filter is client-side over the loaded pages, and switching it
 * on deliberately disables the auto-paging sentinel (an empty filtered list
 * would keep the sentinel in view and drain the backend one page at a time)
 * in favour of an explicit button.
 */
export default function MobileThreadListPage() {
  const { t } = useI18n();
  const {
    data: infiniteThreads,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteThreads();
  const threads = useMemo(
    () => infiniteThreads?.pages.flat() ?? [],
    [infiniteThreads],
  );

  const { isPinned, togglePin } = usePinnedThreads();
  const { mutate: renameThread } = useRenameThread();
  const { mutate: deleteThread } = useDeleteThread();

  const [search, setSearch] = useState("");
  const isSearching = search.trim().length > 0;

  useEffect(() => {
    document.title = `${t.pages.chats} - ${t.pages.appName}`;
  }, [t.pages.chats, t.pages.appName]);

  const filteredThreads = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) {
      return threads;
    }
    return threads.filter((thread) =>
      titleOfThread(thread).toLowerCase().includes(query),
    );
  }, [threads, search]);

  const groups = useMemo(
    () =>
      groupThreads(filteredThreads, {
        getUpdatedAt: (thread) => thread.updated_at,
        isPinned: (thread) => isPinned(thread.thread_id),
        now: new Date(),
      }),
    [filteredThreads, isPinned],
  );

  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const element = sentinelRef.current;
    if (!element || !hasNextPage || isSearching) {
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          void fetchNextPage();
        }
      },
      { rootMargin: "200px 0px 200px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage, isSearching]);

  const handleRename = useCallback(
    (threadId: string, title: string) => {
      renameThread({ threadId, title });
    },
    [renameThread],
  );

  // The list root is never itself a chat route, so the desktop sidebar's
  // "deleted the thread I am looking at" reset (`resetThreadChatAfterDelete`
  // + `router.replace`) has no equivalent here — there is nothing to reset or
  // navigate away from.
  const handleDelete = useCallback(
    (threadId: string) => {
      deleteThread({ threadId });
    },
    [deleteThread],
  );

  const groupLabels: Record<ThreadGroupId, string> = {
    pinned: t.chats.pinned,
    today: t.chats.today,
    yesterday: t.chats.yesterday,
    earlier: t.chats.earlier,
  };

  return (
    <div className="flex min-h-full flex-col">
      {/* Public path: `<Link>` issues an RSC request the middleware rewrites
          onto `/m/workspace/chats/new`, so the address bar stays `/workspace`. */}
      <header className="bg-background/95 sticky top-0 z-10 flex items-center gap-2 border-b px-4 py-2 backdrop-blur">
        <h1 className="min-w-0 flex-1 truncate text-lg font-semibold">
          {t.pages.chats}
        </h1>
        <Link
          href="/workspace/chats/new"
          aria-label={t.pages.newChat}
          data-testid="mobile-new-chat"
          className="active:bg-accent flex size-11 items-center justify-center rounded-full"
        >
          <PlusIcon className="size-5" />
        </Link>
      </header>

      <div className="px-4 py-3">
        <div className="relative">
          <SearchIcon
            aria-hidden="true"
            className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
          />
          <input
            type="search"
            data-testid="mobile-thread-search"
            placeholder={t.chats.searchChats}
            aria-label={t.chats.searchChats}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            // `text-base` (16px) is the iOS floor: anything smaller and Safari
            // zooms the page on focus. `h-12` clears the 44px touch target.
            className={cn(
              "border-input bg-card text-foreground placeholder:text-muted-foreground h-12 w-full min-w-0 rounded-xl border pr-3 pl-9 text-base outline-none",
              "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
            )}
          />
        </div>
      </div>

      <div
        data-testid="mobile-thread-list"
        className="flex min-h-0 flex-1 flex-col pb-[calc(env(safe-area-inset-bottom)+1rem)]"
      >
        {filteredThreads.length === 0 ? (
          <p className="text-muted-foreground px-4 py-10 text-center text-sm">
            {isSearching ? t.chats.noSearchResults : t.chats.empty}
          </p>
        ) : (
          groups.map((group) => (
            <section key={group.id} data-testid="mobile-thread-group">
              <h2 className="bg-background text-muted-foreground px-4 pt-4 pb-1.5 text-[13px] font-medium">
                {groupLabels[group.id]}
              </h2>
              <ul className="flex flex-col">
                {group.threads.map((thread) => (
                  <ThreadRow
                    key={thread.thread_id}
                    thread={thread}
                    pinned={isPinned(thread.thread_id)}
                    onTogglePin={togglePin}
                    onRename={handleRename}
                    onDelete={handleDelete}
                  />
                ))}
              </ul>
            </section>
          ))
        )}

        {hasNextPage && !isSearching && (
          <div
            ref={sentinelRef}
            aria-hidden="true"
            className="h-px w-full"
            data-testid="mobile-thread-list-sentinel"
          />
        )}
        {hasNextPage && isSearching && (
          <div className="flex justify-center p-4">
            <Button
              variant="outline"
              className="h-12 text-base"
              onClick={() => void fetchNextPage()}
              disabled={isFetchingNextPage}
              data-testid="mobile-thread-list-load-more"
            >
              {isFetchingNextPage
                ? t.chats.loadingMore
                : t.chats.loadMoreToSearch}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
