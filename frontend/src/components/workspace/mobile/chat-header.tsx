"use client";

import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { ArrowLeftIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ExportTrigger } from "@/components/workspace/export-trigger";
import { QuotaIndicator } from "@/components/workspace/quota-indicator";
import { SessionStatusButton } from "@/components/workspace/session-status-dialog";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { useI18n } from "@/core/i18n/hooks";
import type { TokenUsage } from "@/core/messages/usage";
import type { TokenUsagePreferences } from "@/core/messages/usage-model";
import { useQuotaMe } from "@/core/quotas/hooks";
import type { AgentThreadState } from "@/core/threads";

/**
 * How the desktop's header tools are laid out inside the mobile sheet.
 *
 * Every one of them is a `Button` sized for a mouse (the session-status button
 * is `size-8`, the token pill is `h-auto`), so each row re-sizes whatever
 * button it hosts through a descendant rule instead of wrapping or editing the
 * shared component — arbitrary variants out-specify the single utility classes
 * the components apply. `min-h-12`/`min-w-12` keep the 44px touch floor.
 */
const CONTROL_ROW_CLASS =
  "[&_button]:min-h-12 [&_button]:min-w-12 [&_button]:text-base [&_button]:font-normal";

type MobileChatHeaderProps = {
  /** Recomputed by `useChatPage()`; `""` for a thread the backend has not titled yet. */
  title: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  isNewThread: boolean;
  isMock: boolean;
  isBusy: boolean;
  pendingUsageMessages: Message[];
  backendTokenUsage: TokenUsage | null;
  tokenUsageEnabled: boolean;
  tokenUsagePreferences: TokenUsagePreferences;
  onTokenUsagePreferencesChange: (preferences: TokenUsagePreferences) => void;
};

/**
 * Mobile chat header (prototype ②): back, title, and a `⋯` sheet.
 *
 * The desktop header spreads six triggers across the top of the page; on a
 * 390px column they would either wrap or shrink below the touch floor, so they
 * move behind one entry point. The tools themselves are the desktop ones —
 * `SessionStatusButton`, `TokenUsageIndicator`, `QuotaIndicator` and
 * `ExportTrigger` all render their own trigger and overlay, so nothing here
 * re-implements their behaviour.
 */
export function MobileChatHeader({
  title,
  threadId,
  thread,
  isNewThread,
  isMock,
  isBusy,
  pendingUsageMessages,
  backendTokenUsage,
  tokenUsageEnabled,
  tokenUsagePreferences,
  onTokenUsagePreferencesChange,
}: MobileChatHeaderProps) {
  const { t } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);
  // `QuotaIndicator` renders nothing at all without a quota payload, which
  // would leave a labelled row with nothing to tap. This is the same hook the
  // indicator uses (same query key, so no extra request) and the same
  // condition it hides on.
  const quota = useQuotaMe();

  // A row whose tool renders nothing on its own (both token indicators return
  // `null` when disabled/absent, export when the thread is empty) would be an
  // empty tap target, so the rows are gated the same way the desktop header
  // gates its triggers.
  const showSessionStatus = !isNewThread && !isMock;
  const showExport = !isMock && thread.messages.length > 0;
  const showQuota =
    !isMock &&
    !quota.isPending &&
    !quota.isError &&
    (quota.data?.items.length ?? 0) > 0;

  return (
    <header className="bg-background/95 sticky top-0 z-20 flex shrink-0 items-center gap-1 border-b px-1 py-1 supports-backdrop-filter:backdrop-blur">
      {/* The public path: middleware re-lands this on `/m/workspace`, so the
          address bar never shows the internal prefix. */}
      <Link
        href="/workspace"
        aria-label={t.pages.chats}
        data-testid="mobile-chat-back"
        className="active:bg-accent flex size-11 shrink-0 items-center justify-center rounded-full"
      >
        <ArrowLeftIcon className="size-5" />
      </Link>
      <h1
        data-testid="mobile-chat-title"
        className="min-w-0 flex-1 truncate text-[15px] font-medium"
      >
        {title || (isNewThread ? t.pages.newChat : t.pages.untitled)}
      </h1>

      <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
        <Button
          type="button"
          variant="ghost"
          aria-label={t.common.more}
          data-testid="mobile-chat-more"
          className="text-muted-foreground size-11 shrink-0 rounded-full p-0"
          onClick={() => setMenuOpen(true)}
        >
          <span aria-hidden="true" className="text-lg leading-none">
            ⋯
          </span>
        </Button>
        <SheetContent
          side="bottom"
          data-testid="mobile-chat-menu"
          // The sheet floats above the tab bar, so it carries the bottom inset
          // itself; `mobile-chat-sheet` re-sizes the shared close button (see
          // `chat-surface.css`).
          className="mobile-chat-sheet pb-[calc(env(safe-area-inset-bottom)+1rem)]"
        >
          <SheetHeader>
            <SheetTitle className="text-base">{t.common.more}</SheetTitle>
          </SheetHeader>
          <div className="flex flex-col gap-1 px-2">
            {showSessionStatus && (
              <div
                className={`flex min-h-12 items-center justify-between gap-3 px-3 ${CONTROL_ROW_CLASS}`}
              >
                <span className="text-base">{t.chats.sessionStatus}</span>
                <SessionStatusButton
                  threadId={threadId}
                  enabled
                  forcePolling={isBusy}
                />
              </div>
            )}
            {tokenUsageEnabled && (
              <div
                className={`flex min-h-12 items-center justify-between gap-3 px-3 ${CONTROL_ROW_CLASS}`}
              >
                <span className="text-base">{t.tokenUsage.title}</span>
                <TokenUsageIndicator
                  threadId={isNewThread ? undefined : threadId}
                  backendUsage={backendTokenUsage}
                  enabled={tokenUsageEnabled}
                  messages={thread.messages}
                  pendingMessages={pendingUsageMessages}
                  preferences={tokenUsagePreferences}
                  onPreferencesChange={onTokenUsagePreferencesChange}
                />
              </div>
            )}
            {showQuota && (
              // `QuotaIndicator` hides its own label below `sm` (the desktop
              // pill is icon-only on narrow screens); inside a full-width row
              // the label is what makes it readable.
              <div
                className={`flex min-h-12 items-center justify-between gap-3 px-3 [&_span]:inline ${CONTROL_ROW_CLASS}`}
              >
                <span className="text-base">{t.quotaIndicator.label}</span>
                <QuotaIndicator isThreadBusy={isBusy} />
              </div>
            )}
            {showExport && (
              <div
                className={`flex min-h-12 items-center px-1 [&_button]:h-12 [&_button]:w-full [&_button]:justify-start [&_button]:gap-3 [&_button]:px-2 [&_button]:text-base [&_button]:font-normal`}
              >
                <ExportTrigger threadId={threadId} />
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </header>
  );
}
