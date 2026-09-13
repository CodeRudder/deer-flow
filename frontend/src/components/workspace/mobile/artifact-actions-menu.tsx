"use client";

import {
  CopyIcon,
  DownloadIcon,
  RefreshCwIcon,
  ShareIcon,
  SquareArrowOutUpRightIcon,
  WrapTextIcon,
} from "lucide-react";
import { type ReactNode, useState } from "react";

import {
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorTrigger,
} from "@/components/ai-elements/model-selector";
import { CommandGroup } from "@/components/ui/command";
import { useI18n } from "@/core/i18n/hooks";

/**
 * The artifact screen's tools, behind the title bar's ⋯ (prototype ⑤b).
 *
 * They used to be a bottom action bar (prototype ⑤'s `artbar`), one 44px cell
 * per tool. That bar cost the document 56px of reading height to hold actions
 * that are not the frequent ones, so it is gone: the ⋯ button at the right of
 * the title bar opens the *same* dialog the file switcher uses — vertically
 * centred, full width, rows in the switcher's own row language — rather than
 * adding a second overlay shape to the screen. ⑤b is the frame this follows;
 * ⑤a stays the switcher's.
 *
 * Every tool is still opt-in, unchanged from the bar: the code view adds copy
 * and the wrap toggle, the image and markup views add open-in-new-window, and
 * a `write-file:` artifact (a draft that only exists in the transcript) has no
 * URL to download from, so it gets neither. A file with no applicable tool
 * renders no ⋯ at all — an empty menu is not an option.
 *
 * Prototype ⑤ draws a fourth key, 全屏 (`requestFullscreen`), which is
 * **deliberately not implemented** (plan T21): a phone browser's own address
 * bar does not go away, so the key would trade a swipe for a locked viewport
 * and gain nothing. The omission is intentional — do not "fix" it.
 */
type MobileArtifactActionsMenuProps = {
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

type MenuRowProps = {
  /**
   * On the element that carries the row's semantics: the link for a row that
   * opens a URL (so its `href` is both assertable and long-pressable), the
   * cmdk row itself for a row that runs a callback.
   */
  testid: string;
  icon: ReactNode;
  label: string;
  /** Runs the tool; the menu closes either way — the result shows behind it. */
  onSelect: () => void;
  disabled?: boolean;
  /** Link rows keep a real `href`, so the browser opens or saves the target. */
  href?: string;
};

/**
 * One row of the tools menu, in the switcher's row language (⑤a): a leading
 * icon, a truncating label, and the same `h-9` height that
 * `mobile-dialog.css` grows to the 44px touch floor for the switcher's file
 * rows and this menu's tools alike.
 */
function MenuRow({
  testid,
  icon,
  label,
  onSelect,
  disabled,
  href,
}: MenuRowProps) {
  const body = (
    <>
      {icon}
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </>
  );

  return (
    <ModelSelectorItem
      data-testid={href ? undefined : testid}
      disabled={disabled}
      onSelect={onSelect}
      // `p-0` on a link row: its anchor owns the padding, so the *anchor* is
      // the full 44px row and there is no dead strip inside the row that
      // selects without navigating. A callback row keeps the registry's own
      // padding, which is also what the switcher's rows use.
      className={href ? "text-foreground h-9 p-0" : "text-foreground h-9"}
    >
      {href ? (
        <a
          data-testid={testid}
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="flex min-h-11 min-w-0 flex-1 items-center gap-2 px-2"
        >
          {body}
        </a>
      ) : (
        body
      )}
    </ModelSelectorItem>
  );
}

export function MobileArtifactActionsMenu({
  downloadUrl,
  openUrl,
  fileName,
  wrap,
  onWrapChange,
  onCopy,
  copyDisabled,
  onRefresh,
}: MobileArtifactActionsMenuProps) {
  const { t } = useI18n();
  // The dialog is controlled, like the switcher's: picking a tool must close
  // it, and cmdk only reports the selection — nothing else tears the dialog
  // down, because a menu item that changes no URL is no navigation.
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);

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

  // Prototype ⑤b's order: 分享、下载、复制到剪贴板、在新窗口打开、自动换行、刷新.
  const rows: ReactNode[] = [];

  if (downloadUrl && fileName) {
    rows.push(
      <MenuRow
        key="share"
        testid="mobile-artifact-share"
        icon={<ShareIcon aria-hidden="true" />}
        label={t.common.share}
        onSelect={() => {
          close();
          void handleShare();
        }}
      />,
    );
  }

  if (downloadUrl) {
    rows.push(
      <MenuRow
        key="download"
        testid="mobile-artifact-download"
        icon={<DownloadIcon aria-hidden="true" />}
        label={t.common.download}
        href={downloadUrl}
        onSelect={close}
      />,
    );
  }

  if (onCopy) {
    rows.push(
      <MenuRow
        key="copy"
        testid="mobile-artifact-copy"
        icon={<CopyIcon aria-hidden="true" />}
        label={t.clipboard.copyToClipboard}
        disabled={copyDisabled}
        onSelect={() => {
          close();
          onCopy();
        }}
      />,
    );
  }

  if (openUrl) {
    rows.push(
      <MenuRow
        key="open"
        testid="mobile-artifact-open"
        icon={<SquareArrowOutUpRightIcon aria-hidden="true" />}
        label={t.common.openInNewWindow}
        href={openUrl}
        onSelect={close}
      />,
    );
  }

  if (onWrapChange) {
    rows.push(
      <MenuRow
        key="wrap"
        testid="mobile-artifact-wrap"
        icon={<WrapTextIcon aria-hidden="true" />}
        // The label is the action, not the state: the row that turns wrapping
        // on says 自动换行, and once it is on the row offers 不换行.
        label={wrap ? t.artifactViewer.noWrapLines : t.artifactViewer.wrapLines}
        onSelect={() => {
          close();
          onWrapChange(!wrap);
        }}
      />,
    );
  }

  if (onRefresh) {
    rows.push(
      <MenuRow
        key="refresh"
        testid="mobile-artifact-refresh"
        icon={<RefreshCwIcon aria-hidden="true" />}
        label={t.artifactViewer.refresh}
        onSelect={() => {
          close();
          onRefresh();
        }}
      />,
    );
  }

  if (rows.length === 0) {
    return null;
  }

  return (
    <ModelSelector open={open} onOpenChange={setOpen}>
      <ModelSelectorTrigger asChild>
        {/* The same ⋯ the chat header carries: one glyph, one touch target. */}
        <button
          type="button"
          data-testid="mobile-artifact-tools"
          aria-label={t.artifactViewer.tools}
          className="active:bg-accent flex size-11 shrink-0 items-center justify-center rounded-full"
        >
          <span aria-hidden="true" className="text-lg leading-none">
            ⋯
          </span>
        </button>
      </ModelSelectorTrigger>
      {/* The switcher's dialog, geometry and all: `.mobile-model-dialog` is
          what makes this the same overlay (16px gutters, 44px rows) rather
          than a second one. The list carries no search box — six tools need
          no filter, unlike a thread's file list. */}
      <ModelSelectorContent
        data-testid="mobile-artifact-tools-menu"
        className="mobile-model-dialog"
        title={t.artifactViewer.tools}
        // The footnote is the dialog's description too, exactly as the
        // switcher passes its search hint as both placeholder and description.
        description={t.artifactViewer.toolsNote}
      >
        <ModelSelectorList
          className="max-h-80 p-1"
          aria-label={t.artifactViewer.tools}
        >
          <CommandGroup
            heading={t.artifactViewer.tools}
            // `p-1` already lives on the list; the group must not add another.
            className="p-0"
          >
            {rows}
          </CommandGroup>
        </ModelSelectorList>
        {/* ⑤b's `dnote`: the tools are per file type, and the menu says so. */}
        <p className="text-muted-foreground mx-1 mb-1 border-t px-2 pt-2 text-[11.5px] leading-normal">
          {t.artifactViewer.toolsNote}
        </p>
      </ModelSelectorContent>
    </ModelSelector>
  );
}
