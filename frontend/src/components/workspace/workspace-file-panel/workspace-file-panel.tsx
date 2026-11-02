"use client";

import {
  ChevronLeftIcon,
  FileArchiveIcon,
  FileIcon,
  FolderIcon,
  ImageIcon,
  LoaderIcon,
  RefreshCwIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip } from "@/components/workspace/tooltip";
import { resolveArtifactURL } from "@/core/artifacts/utils";
import { useI18n } from "@/core/i18n/hooks";
import { formatTimeAgo } from "@/core/utils/datetime";
import { isImageFile } from "@/core/utils/files";
import { cn } from "@/lib/utils";

import {
  fetchWorkspaceListing,
  WORKSPACE_ROOT_PATH,
  WorkspaceDirectoryNotFoundError,
  type WorkspaceEntry,
  type WorkspaceListing,
} from "./api";

const SEARCH_DEBOUNCE_MS = 300;

/** Human-readable byte size, e.g. `1.2 KB`. `null` (directories) → empty. */
export function formatFileSize(size: number | null): string {
  if (size === null || Number.isNaN(size)) return "";
  if (size < 1024) return `${size} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = size;
  let unit = -1;
  do {
    value /= 1024;
    unit += 1;
  } while (value >= 1024 && unit < units.length - 1);
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

/** Date-only modification time display; returns "" for missing values. */
export function formatModifiedAt(modifiedAt: string | null): string {
  if (!modifiedAt) return "";
  const date = new Date(modifiedAt);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString();
}

const THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000;

/**
 * Local-timezone mtime display: relative ("3 minutes ago") for recent
 * entries, absolute date once older than ~30 days. "" for missing values.
 */
export function formatEntryModifiedAt(modifiedAt: string | null): string {
  if (!modifiedAt) return "";
  const date = new Date(modifiedAt);
  if (Number.isNaN(date.getTime())) return "";
  const age = Date.now() - date.getTime();
  if (age < 0 || age > THIRTY_DAYS_MS) return formatModifiedAt(modifiedAt);
  return formatTimeAgo(date);
}

function FileIconForEntry({ entry }: { entry: WorkspaceEntry }) {
  if (entry.type === "directory") {
    return <FolderIcon className="text-muted-foreground size-4 shrink-0" />;
  }
  const extension = entry.name.split(".").pop()?.toLowerCase() ?? "";
  if (
    ["zip", "tar", "gz", "tgz", "bz2", "xz", "rar", "7z"].includes(extension)
  ) {
    return (
      <FileArchiveIcon className="text-muted-foreground size-4 shrink-0" />
    );
  }
  if (isImageFile(entry.name)) {
    return <ImageIcon className="text-muted-foreground size-4 shrink-0" />;
  }
  return <FileIcon className="text-muted-foreground size-4 shrink-0" />;
}

interface WorkspaceFilePanelProps {
  threadId: string;
  open: boolean;
  onClose?: () => void;
}

/**
 * Right-side collapsible sidebar browsing the thread workspace files.
 *
 * Desktop (≥md) it renders as a third flex column; below md it renders as a
 * fixed overlay drawer with a backdrop. Closed → renders nothing. Listing is
 * manual-only: it loads when opened / navigated and refreshes only via the
 * explicit refresh button (no realtime polling).
 */
export function WorkspaceFilePanel({
  threadId,
  open,
  onClose,
}: WorkspaceFilePanelProps) {
  const { t } = useI18n();
  const [path, setPath] = useState(WORKSPACE_ROOT_PATH);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [listing, setListing] = useState<WorkspaceListing | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isNotFound, setIsNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewEntry, setPreviewEntry] = useState<WorkspaceEntry | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);
  const requestSeq = useRef(0);

  // Debounce the search input; emptying it returns to directory browsing.
  useEffect(() => {
    const timer = setTimeout(
      () => setDebouncedSearch(search.trim()),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [search]);

  const load = useCallback(
    async (dirPath: string, searchKeyword: string) => {
      const seq = ++requestSeq.current;
      setIsLoading(true);
      setError(null);
      setIsNotFound(false);
      try {
        const result = await fetchWorkspaceListing(
          threadId,
          dirPath,
          searchKeyword || undefined,
        );
        if (seq !== requestSeq.current) return;
        setListing(result);
        setPath(result.path || dirPath);
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setListing(null);
        if (err instanceof WorkspaceDirectoryNotFoundError) {
          setIsNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (seq === requestSeq.current) setIsLoading(false);
      }
    },
    [threadId],
  );

  // (Re)load on open, navigation, debounced search or explicit refresh.
  useEffect(() => {
    if (!open) return;
    void load(path, debouncedSearch);
  }, [open, path, debouncedSearch, refreshTick, load]);

  const handleClose = useCallback(() => {
    onClose?.();
  }, [onClose]);

  const handleEntryClick = useCallback(
    (entry: WorkspaceEntry) => {
      if (entry.type === "directory") {
        setSearch("");
        setDebouncedSearch("");
        setPath(entry.path);
        return;
      }
      if (isImageFile(entry.name)) {
        setPreviewEntry(entry);
        return;
      }
      window.open(
        `${resolveArtifactURL(entry.path, threadId)}?download=true`,
        "_blank",
        "noopener",
      );
    },
    [threadId],
  );

  // Breadcrumb segments relative to the virtual root ("/mnt/user-data").
  const segments = path.startsWith(WORKSPACE_ROOT_PATH)
    ? path.slice(WORKSPACE_ROOT_PATH.length).split("/").filter(Boolean)
    : [path];
  const parentPath = (() => {
    if (path === WORKSPACE_ROOT_PATH) return null;
    const idx = path.lastIndexOf("/");
    return idx > 0 ? path.slice(0, idx) : null;
  })();

  const isSearching = debouncedSearch.length > 0;

  const body = (() => {
    if (isLoading && !listing) {
      return (
        <div
          data-testid="workspace-files-loading"
          className="text-muted-foreground flex flex-1 items-center justify-center gap-2 p-4 text-sm"
        >
          <LoaderIcon className="size-4 animate-spin" />
          {t.workspaceFiles.loading}
        </div>
      );
    }
    if (isNotFound) {
      return (
        <div
          data-testid="workspace-files-not-found"
          className="text-muted-foreground flex flex-1 items-center justify-center p-4 text-center text-sm"
        >
          {t.workspaceFiles.notFound}
        </div>
      );
    }
    if (error) {
      return (
        <div
          data-testid="workspace-files-error"
          className="flex flex-1 flex-col items-center justify-center gap-2 p-4 text-center text-sm"
        >
          <span className="text-destructive">{t.workspaceFiles.loadError}</span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setRefreshTick((tick) => tick + 1)}
          >
            <RefreshCwIcon className="size-3.5" />
            {t.common.retry}
          </Button>
        </div>
      );
    }
    const entries = listing?.entries ?? [];
    if (entries.length === 0) {
      return (
        <div
          data-testid="workspace-files-empty"
          className="text-muted-foreground flex flex-1 items-center justify-center p-4 text-center text-sm"
        >
          {isSearching ? t.workspaceFiles.noResults : t.workspaceFiles.empty}
        </div>
      );
    }
    return (
      <ul className="flex-1 overflow-y-auto" data-testid="workspace-files-list">
        {entries.map((entry) => (
          <li key={entry.path}>
            <button
              type="button"
              className="hover:bg-accent flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm"
              onClick={() => handleEntryClick(entry)}
              title={isSearching ? entry.path : undefined}
            >
              <FileIconForEntry entry={entry} />
              <span className="min-w-0 flex-1 truncate">
                {isSearching
                  ? entry.path.replace(WORKSPACE_ROOT_PATH, "")
                  : entry.name}
              </span>
              <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                {formatFileSize(entry.size)}
              </span>
              <span className="text-muted-foreground hidden shrink-0 text-xs sm:inline">
                {formatEntryModifiedAt(entry.modified_at)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    );
  })();

  const header = (
    <div className="flex flex-col gap-2 border-b px-3 py-2">
      <div className="flex items-center gap-1">
        <Tooltip content={t.workspaceFiles.up}>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            disabled={path === WORKSPACE_ROOT_PATH}
            onClick={() => parentPath && setPath(parentPath)}
            data-testid="workspace-files-up"
          >
            <ChevronLeftIcon className="size-4" />
          </Button>
        </Tooltip>
        <nav
          className="min-w-0 flex-1 truncate text-sm font-medium"
          data-testid="workspace-files-breadcrumbs"
        >
          <button
            type="button"
            className="hover:text-foreground hover:underline"
            onClick={() => {
              setSearch("");
              setDebouncedSearch("");
              setPath(WORKSPACE_ROOT_PATH);
            }}
          >
            {t.workspaceFiles.rootName}
          </button>
          {segments.map((segment, index) => {
            const segmentPath =
              WORKSPACE_ROOT_PATH +
              "/" +
              segments.slice(0, index + 1).join("/");
            return (
              <span key={segmentPath}>
                <span className="text-muted-foreground mx-0.5">/</span>
                <button
                  type="button"
                  className="hover:text-foreground hover:underline"
                  onClick={() => {
                    setSearch("");
                    setDebouncedSearch("");
                    setPath(segmentPath);
                  }}
                >
                  {segment}
                </button>
              </span>
            );
          })}
        </nav>
        <Tooltip content={t.workspaceFiles.refresh}>
          <Button
            variant="outline"
            size="icon"
            className="size-7"
            onClick={() => setRefreshTick((tick) => tick + 1)}
            data-testid="workspace-files-refresh"
          >
            <RefreshCwIcon
              className={cn("size-4", isLoading && "animate-spin")}
            />
          </Button>
        </Tooltip>
        {onClose && (
          <Tooltip content={t.common.close}>
            <Button
              variant="ghost"
              size="icon"
              className="size-7 md:hidden"
              onClick={handleClose}
              data-testid="workspace-files-close"
            >
              <XIcon className="size-4" />
            </Button>
          </Tooltip>
        )}
      </div>
      <div className="relative">
        <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t.workspaceFiles.searchPlaceholder}
          className="h-7 pl-7 text-xs"
          data-testid="workspace-files-search"
        />
      </div>
    </div>
  );

  const footer = (
    <div className="text-muted-foreground border-t px-3 py-1.5 text-xs">
      {listing?.truncated ? (
        <span data-testid="workspace-files-truncated">
          {t.workspaceFiles.truncated}
        </span>
      ) : (
        <span>{t.workspaceFiles.manualRefreshHint}</span>
      )}
    </div>
  );

  if (!open) return null;

  return (
    <>
      {/* Desktop: third flex column next to the message area. */}
      <aside
        data-testid="workspace-files-panel"
        className="bg-background hidden w-80 shrink-0 flex-col border-l md:flex"
      >
        {header}
        {body}
        {footer}
      </aside>
      {/* Mobile: overlay drawer with a backdrop. */}
      <div className="fixed inset-0 z-50 md:hidden">
        <div
          className="absolute inset-0 bg-black/40"
          onClick={handleClose}
          data-testid="workspace-files-backdrop"
        />
        <div className="bg-background absolute top-0 right-0 flex h-full w-80 max-w-full flex-col border-l shadow-lg">
          {header}
          {body}
          {footer}
        </div>
      </div>
      {/* Simple image preview dialog. */}
      {previewEntry && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          data-testid="workspace-files-image-preview"
          onClick={() => setPreviewEntry(null)}
        >
          <div className="absolute inset-0 bg-black/60" />
          <img
            src={`${resolveArtifactURL(previewEntry.path, threadId)}?w=720&fmt=jpg`}
            alt={previewEntry.name}
            className="relative max-h-full max-w-full rounded-lg shadow-lg"
            onClick={(event) => event.stopPropagation()}
          />
          <Button
            variant="secondary"
            size="icon"
            className="absolute top-4 right-4"
            onClick={() => setPreviewEntry(null)}
          >
            <XIcon className="size-4" />
          </Button>
        </div>
      )}
    </>
  );
}
