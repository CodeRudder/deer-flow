"use client";

import {
  CopyIcon,
  DownloadIcon,
  SquareArrowOutUpRightIcon,
  WrapTextIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/**
 * Bottom action bar of the mobile artifact screen (prototype ⑤'s `artbar`).
 *
 * The desktop puts these actions in the artifact panel's header, where they
 * are 32px icon buttons on a mouse-sized toolbar. On a phone they move to
 * thumb reach and grow to the 44px touch floor, and the bar carries the bottom
 * safe-area inset because — unlike the chat screen — the screen is full-bleed
 * and the tab bar is hidden (`tab-bar.tsx`).
 *
 * Every action is opt-in: the code view adds copy and the wrap toggle, the
 * image and markup views add open-in-new-window, and a `write-file:` artifact
 * (a draft that only exists in the transcript) has no URL to download from, so
 * it gets neither.
 */
type MobileArtifactActionsProps = {
  className?: string;
  /** Opens in a new tab; omitted for artifacts with no fetchable URL. */
  downloadUrl?: string;
  openUrl?: string;
  /** Code view: shift long lines to the next line instead of scrolling. */
  wrap?: boolean;
  onWrapChange?: (wrap: boolean) => void;
  /** Code view: copy the source. */
  onCopy?: () => void;
  copyDisabled?: boolean;
};

/** One cell of the bar: icon over label, above the 44px floor. */
const ACTION_CLASS =
  "flex min-h-11 min-w-11 flex-1 flex-col items-center justify-center gap-0.5 px-1 py-1 text-center text-[11px] leading-tight disabled:opacity-40";

export function MobileArtifactActions({
  className,
  downloadUrl,
  openUrl,
  wrap,
  onWrapChange,
  onCopy,
  copyDisabled,
}: MobileArtifactActionsProps) {
  const { t } = useI18n();

  const actions: ReactNode[] = [];

  if (onWrapChange) {
    actions.push(
      <button
        key="wrap"
        type="button"
        data-testid="mobile-artifact-wrap"
        aria-pressed={wrap ?? false}
        onClick={() => onWrapChange(!wrap)}
        className={cn(ACTION_CLASS, "active:bg-accent rounded-md")}
      >
        <WrapTextIcon aria-hidden="true" className="size-5" />
        <span>
          {wrap ? t.artifactViewer.noWrapLines : t.artifactViewer.wrapLines}
        </span>
      </button>,
    );
  }

  if (onCopy) {
    actions.push(
      <button
        key="copy"
        type="button"
        data-testid="mobile-artifact-copy"
        disabled={copyDisabled}
        onClick={onCopy}
        className={cn(ACTION_CLASS, "active:bg-accent rounded-md")}
      >
        <CopyIcon aria-hidden="true" className="size-5" />
        <span>{t.clipboard.copyToClipboard}</span>
      </button>,
    );
  }

  if (openUrl) {
    actions.push(
      <a
        key="open"
        data-testid="mobile-artifact-open"
        href={openUrl}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(ACTION_CLASS, "active:bg-accent rounded-md")}
      >
        <SquareArrowOutUpRightIcon aria-hidden="true" className="size-5" />
        <span>{t.common.openInNewWindow}</span>
      </a>,
    );
  }

  if (downloadUrl) {
    actions.push(
      <a
        key="download"
        data-testid="mobile-artifact-download"
        href={downloadUrl}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(ACTION_CLASS, "active:bg-accent rounded-md")}
      >
        <DownloadIcon aria-hidden="true" className="size-5" />
        <span>{t.common.download}</span>
      </a>,
    );
  }

  if (actions.length === 0) {
    return null;
  }

  return (
    <div
      data-testid="mobile-artifact-actions"
      className={cn(
        "bg-background flex shrink-0 items-stretch gap-1 border-t px-2 pt-1 pb-[env(safe-area-inset-bottom)]",
        className,
      )}
    >
      {actions}
    </div>
  );
}
