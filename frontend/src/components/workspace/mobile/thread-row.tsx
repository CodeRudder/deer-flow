"use client";

import { Pin, PinOff, Pencil, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { MobileAuthField } from "@/components/workspace/mobile/auth-field";
import { useI18n } from "@/core/i18n/hooks";
import type { AgentThread } from "@/core/threads/types";
import {
  lastMessagePreview,
  pathOfThread,
  titleOfThread,
} from "@/core/threads/utils";
import { formatTimeAgo } from "@/core/utils/datetime";
import { cn } from "@/lib/utils";

/**
 * How long a finger must rest on a row before the action sheet opens. The
 * prototype's note ("长按任一行出操作菜单") sets the interaction, the plan sets
 * the floor — 500ms is the same order as the platform long-press (~500ms on
 * both iOS and Android), so it does not feel early.
 */
export const LONG_PRESS_MS = 500;

/**
 * Movement past this many CSS pixels turns the gesture into a scroll and
 * cancels the pending long-press. Without it, flicking the list would open a
 * sheet on whichever row the finger happened to rest on first.
 */
export const LONG_PRESS_MOVE_TOLERANCE_PX = 10;

type ThreadRowProps = {
  thread: AgentThread;
  pinned: boolean;
  onTogglePin: (threadId: string) => void;
  onRename: (threadId: string, title: string) => void;
  onDelete: (threadId: string) => void;
};

/**
 * True when the thread has a run in flight (prototype ①'s「● 生成中」).
 *
 * No extra request: `threads.search` already returns a per-thread `status`
 * (the gateway's `ThreadResponse.status`, read straight off `threads_meta`).
 * The gateway writes `running` when the run is created
 * (`app/gateway/services.py`) and resets the row to `idle` — or to the run's
 * terminal status — when it ends (`deerflow/runtime/runs/worker.py`), with a
 * startup reconciliation marking crash-orphaned runs `error`
 * (`app/gateway/deps.py`). `busy` is the LangGraph Platform word for the same
 * state, which is what `useThreadStream`'s optimistic `onCreated` upsert writes
 * into the thread caches, so both are accepted.
 *
 * Read as a plain string: the SDK types `status` as `ThreadStatus`
 * (`idle | busy | interrupted | error`), a narrower union than the gateway's
 * run-lifecycle values, so narrowing to either dialect would not type-check.
 */
export function isThreadGenerating(
  thread: Pick<AgentThread, "status">,
): boolean {
  const status: string = thread.status;
  return status === "running" || status === "busy";
}

/**
 * One row of the mobile thread list (prototype ①).
 *
 * The desktop sidebar hangs its actions off a `DropdownMenu` that only appears
 * on hover (`SidebarMenuAction showOnHover`) — on a touch screen there is no
 * hover, so the trigger would be unreachable. Long-press is the touch-native
 * equivalent, and the sheet it opens carries the same three actions.
 *
 * Gesture notes (plan rule 3 — long-press must not fight text selection):
 * - the row is `select-none` and suppresses `-webkit-touch-callout`, so a
 *   resting finger cannot start a selection or raise the native share sheet;
 * - `onContextMenu` is prevented for the same reason on Android/desktop;
 * - the action sheet is a portal, so the input inside it is outside the row's
 *   suppression and remains normally selectable.
 */
export function ThreadRow({
  thread,
  pinned,
  onTogglePin,
  onRename,
  onDelete,
}: ThreadRowProps) {
  const { t, locale } = useI18n();
  const [sheetOpen, setSheetOpen] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pressOrigin = useRef<{ x: number; y: number } | null>(null);
  // Set when the long-press wins the race against the tap: the pointerup that
  // follows must not also navigate into the chat.
  const suppressClick = useRef(false);

  const cancelLongPress = useCallback(() => {
    if (longPressTimer.current !== null) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
    pressOrigin.current = null;
  }, []);

  useEffect(() => cancelLongPress, [cancelLongPress]);

  const openActions = useCallback(() => {
    suppressClick.current = true;
    setRenameValue(titleOfThread(thread));
    setIsRenaming(false);
    setSheetOpen(true);
  }, [thread]);

  const handlePointerDown = useCallback(
    (event: React.PointerEvent<HTMLAnchorElement>) => {
      // Secondary buttons are not a long press (and on desktop the mouse is a
      // pointer too, where right-click has its own context-menu path).
      if (event.button !== 0) {
        return;
      }
      suppressClick.current = false;
      pressOrigin.current = { x: event.clientX, y: event.clientY };
      longPressTimer.current = setTimeout(() => {
        longPressTimer.current = null;
        openActions();
      }, LONG_PRESS_MS);
    },
    [openActions],
  );

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLAnchorElement>) => {
      const origin = pressOrigin.current;
      if (!origin) {
        return;
      }
      const moved =
        Math.abs(event.clientX - origin.x) > LONG_PRESS_MOVE_TOLERANCE_PX ||
        Math.abs(event.clientY - origin.y) > LONG_PRESS_MOVE_TOLERANCE_PX;
      if (moved) {
        cancelLongPress();
      }
    },
    [cancelLongPress],
  );

  const handleClick = useCallback((event: React.MouseEvent) => {
    if (suppressClick.current) {
      suppressClick.current = false;
      event.preventDefault();
    }
  }, []);

  const preview = lastMessagePreview(thread);
  const isGenerating = isThreadGenerating(thread);
  const updatedAtAgo = thread.updated_at
    ? formatTimeAgo(thread.updated_at, locale)
    : null;

  const handleRenameSubmit = useCallback(() => {
    const nextTitle = renameValue.trim();
    if (!nextTitle) {
      return;
    }
    onRename(thread.thread_id, nextTitle);
    setSheetOpen(false);
  }, [onRename, renameValue, thread.thread_id]);

  return (
    <li
      data-testid="mobile-thread-row"
      data-thread-id={thread.thread_id}
      // The action button is a flex sibling rather than an overlay: positioned
      // absolutely it would sit on top of the timestamp on a 390px column.
      //
      // A running thread tints the *whole* row — prototype ① paints
      // `background: var(--accent)` on the row, not just the link — so the
      // highlight also covers the ⋯ button's column.
      className={cn("flex items-center", isGenerating && "bg-accent")}
    >
      <Link
        href={pathOfThread(thread)}
        // 56px tall, comfortably over the 44px floor, and the whole row minus
        // the action button is the touch target rather than just the title.
        className={cn(
          "active:bg-accent flex min-h-14 min-w-0 flex-1 items-center gap-3 py-3 pr-1 pl-4 select-none",
          "[-webkit-touch-callout:none]",
        )}
        onClick={handleClick}
        onContextMenu={(event) => event.preventDefault()}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={cancelLongPress}
        onPointerCancel={cancelLongPress}
        onPointerLeave={cancelLongPress}
        onKeyDown={(event) => {
          // Keyboard users get the same menu without a pointer: the row is a
          // link, so the sheet hangs off a dedicated affordance instead —
          // see the more-button below.
          if (
            event.key === "ContextMenu" ||
            (event.shiftKey && event.key === "F10")
          ) {
            event.preventDefault();
            openActions();
          }
        }}
      >
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex min-w-0 items-center gap-1.5">
            {pinned && (
              <Pin
                aria-hidden="true"
                className="text-muted-foreground size-3.5 shrink-0"
              />
            )}
            <span className="truncate text-[15px] font-medium">
              {titleOfThread(thread)}
            </span>
          </span>
          {(preview !== null || isGenerating) && (
            // Prototype ① reads `…● 生成中` on one line: the preview keeps as
            // much width as it can and the state follows the text rather than
            // being pushed to the far edge. A brand-new thread whose first run
            // is still in flight has no preview at all, so the state alone is
            // enough to draw the line.
            <span className="text-muted-foreground flex min-w-0 items-center gap-1 text-[13px]">
              {preview && <span className="min-w-0 truncate">{preview}</span>}
              {isGenerating && (
                <span
                  data-testid="mobile-thread-generating"
                  className="flex shrink-0 items-center gap-1"
                >
                  <span aria-hidden="true">●</span>
                  {t.chats.generating}
                </span>
              )}
            </span>
          )}
        </span>
        {updatedAtAgo && (
          <span className="text-muted-foreground shrink-0 text-[11.5px]">
            {updatedAtAgo}
          </span>
        )}
      </Link>

      {/* Touch has no hover, so the sheet also needs a permanently visible
          trigger; it is the second path to the same actions. */}
      <button
        type="button"
        data-testid="mobile-thread-row-more"
        aria-label={t.chats.actions}
        onClick={openActions}
        className="text-muted-foreground active:bg-accent mr-1 flex size-11 shrink-0 items-center justify-center rounded-full"
      >
        <span aria-hidden="true" className="text-lg leading-none">
          ⋯
        </span>
      </button>

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent
          side="bottom"
          data-testid="mobile-thread-actions-sheet"
          className="pb-[calc(env(safe-area-inset-bottom)+1rem)]"
        >
          <SheetHeader>
            <SheetTitle className="truncate pr-8 text-base">
              {titleOfThread(thread)}
            </SheetTitle>
          </SheetHeader>

          {isRenaming ? (
            <div className="flex flex-col gap-3 px-4">
              <MobileAuthField
                autoFocus
                aria-label={t.common.rename}
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    handleRenameSubmit();
                  }
                }}
              />
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  className="h-12 flex-1 text-base"
                  onClick={() => setIsRenaming(false)}
                >
                  {t.common.cancel}
                </Button>
                <Button
                  className="h-12 flex-1 text-base"
                  onClick={handleRenameSubmit}
                  disabled={renameValue.trim().length === 0}
                >
                  {t.common.save}
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-1 px-2">
              <Button
                variant="ghost"
                data-testid="mobile-thread-action-pin"
                className="h-12 justify-start gap-3 px-3 text-base font-normal"
                onClick={() => {
                  onTogglePin(thread.thread_id);
                  setSheetOpen(false);
                }}
              >
                {pinned ? <PinOff /> : <Pin />}
                {pinned ? t.chats.unpin : t.chats.pin}
              </Button>
              <Button
                variant="ghost"
                data-testid="mobile-thread-action-rename"
                className="h-12 justify-start gap-3 px-3 text-base font-normal"
                onClick={() => setIsRenaming(true)}
              >
                <Pencil />
                {t.common.rename}
              </Button>
              <Button
                variant="ghost"
                data-testid="mobile-thread-action-delete"
                className="text-destructive h-12 justify-start gap-3 px-3 text-base font-normal"
                onClick={() => {
                  setSheetOpen(false);
                  setConfirmDeleteOpen(true);
                }}
              >
                <Trash2 />
                {t.common.delete}
              </Button>
            </div>
          )}
        </SheetContent>
      </Sheet>

      {/* Delete keeps a confirmation step: on a phone the sheet covers the row
          it came from, so an accidental tap is easy and unrecoverable. */}
      <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.chats.deleteConfirmTitle}</AlertDialogTitle>
            <AlertDialogDescription>
              {t.chats.deleteConfirm}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              data-testid="mobile-thread-delete-confirm"
              onClick={() => onDelete(thread.thread_id)}
            >
              {t.common.delete}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  );
}
