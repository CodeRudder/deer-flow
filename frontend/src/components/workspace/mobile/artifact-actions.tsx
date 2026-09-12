"use client";

import {
  CopyIcon,
  DownloadIcon,
  RefreshCwIcon,
  ShareIcon,
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
 * and there is no tab bar under it: the route sits in `(app)/(fullbleed)`,
 * which does not render one (T17).
 *
 * Every action is opt-in: the code view adds copy and the wrap toggle, the
 * image and markup views add open-in-new-window, and a `write-file:` artifact
 * (a draft that only exists in the transcript) has no URL to download from, so
 * it gets neither.
 *
 * Prototype ⑤ draws a fourth key, 全屏 (`requestFullscreen`), which is
 * **deliberately not implemented** (plan T21): a phone browser's own address
 * bar does not go away, so the key would trade a swipe for a locked viewport
 * and gain nothing. The omission is intentional — do not "fix" it.
 */
type MobileArtifactActionsProps = {
  className?: string;
  /** Opens in a new tab; omitted for artifacts with no fetchable URL. */
  downloadUrl?: string;
  openUrl?: string;
  /** Filename the share sheet shows, and what the file is called once saved. */
  fileName?: string;
  /** Code view: shift long lines to the next line instead of scrolling. */
  wrap?: boolean;
  onWrapChange?: (wrap: boolean) => void;
  /** Code view: copy the source. */
  onCopy?: () => void;
  copyDisabled?: boolean;
  /** Re-fetches the artifact's bytes; omitted when nothing is fetched. */
  onRefresh?: () => void;
};

/** One cell of the bar: icon over label, above the 44px floor. */
const ACTION_CLASS =
  "flex min-h-11 min-w-11 flex-1 flex-col items-center justify-center gap-0.5 px-1 py-1 text-center text-[11px] leading-tight disabled:opacity-40";

/** The slice of `navigator` the share decision reads. */
export type ShareCapableNavigator = {
  share?: (data?: ShareData) => Promise<void>;
  canShare?: (data?: ShareData) => boolean;
};

/**
 * Whether this browser can hand *the file itself* to the OS share sheet.
 *
 * Both halves are load-bearing. `navigator.share` is absent on desktop Firefox
 * and on any non-secure origin, and `canShare({ files })` is the only way to
 * learn — before the tap is committed to — that the platform accepts a file
 * payload; calling `share` first would reject after the user has already
 * gestured. Written as a function of the environment it is handed, so both
 * answers are unit-testable without a browser.
 */
export function canShareFiles(
  nav: ShareCapableNavigator | null | undefined,
  file: File | null | undefined,
): boolean {
  if (
    !file ||
    typeof nav?.share !== "function" ||
    typeof nav.canShare !== "function"
  ) {
    return false;
  }
  try {
    return nav.canShare({ files: [file] });
  } catch {
    // A payload the platform cannot even inspect is a "no".
    return false;
  }
}

/** The artifact's bytes as the `File` a share sheet can take. */
async function loadShareFile(
  url: string,
  fileName: string,
): Promise<File | null> {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      return null;
    }
    const blob = await response.blob();
    return new File([blob], fileName, {
      type: blob.type || "application/octet-stream",
    });
  } catch {
    // Offline, blocked or opaque: the caller falls back to the download.
    return null;
  }
}

/** Cancelling the share sheet rejects; that is a finished action, not a failure. */
function isShareCancellation(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/**
 * The download action's own behaviour, for the fallback that has no `<a>` to
 * click: `?download=true` answers with `Content-Disposition: attachment`, so
 * the new tab saves the file and closes itself.
 */
function triggerDownload(url: string) {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

export function MobileArtifactActions({
  className,
  downloadUrl,
  openUrl,
  fileName,
  wrap,
  onWrapChange,
  onCopy,
  copyDisabled,
  onRefresh,
}: MobileArtifactActionsProps) {
  const { t } = useI18n();

  const actions: ReactNode[] = [];

  /**
   * 分享: the share sheet when the platform can take the file, the download
   * otherwise (A5). The bytes are fetched first because a share sheet takes
   * files, not URLs — and the artifact URL needs this session's cookie, so a
   * link would be useless to the recipient anyway. Every failure path ends in
   * the download, so a tap never does nothing.
   */
  const handleShare = async () => {
    if (!downloadUrl || !fileName) {
      return;
    }
    const file = await loadShareFile(downloadUrl, fileName);
    if (file && canShareFiles(navigator, file)) {
      try {
        await navigator.share?.({ files: [file], title: fileName });
        return;
      } catch (error) {
        if (isShareCancellation(error)) {
          return;
        }
        // Sharing refused the payload: fall through to the download.
      }
    }
    triggerDownload(downloadUrl);
  };

  if (downloadUrl && fileName) {
    actions.push(
      <button
        key="share"
        type="button"
        data-testid="mobile-artifact-share"
        onClick={() => {
          void handleShare();
        }}
        className={cn(ACTION_CLASS, "active:bg-accent rounded-md")}
      >
        <ShareIcon aria-hidden="true" className="size-5" />
        <span>{t.common.share}</span>
      </button>,
    );
  }

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

  if (onRefresh) {
    actions.push(
      <button
        key="refresh"
        type="button"
        data-testid="mobile-artifact-refresh"
        onClick={onRefresh}
        className={cn(ACTION_CLASS, "active:bg-accent rounded-md")}
      >
        <RefreshCwIcon aria-hidden="true" className="size-5" />
        <span>{t.artifactViewer.refresh}</span>
      </button>,
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
