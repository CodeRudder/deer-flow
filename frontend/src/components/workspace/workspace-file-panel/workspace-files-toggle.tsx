"use client";

import { FolderIcon, FolderOpenIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/workspace/tooltip";
import { useI18n } from "@/core/i18n/hooks";

export function WorkspaceFilesToggle({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useI18n();
  return (
    <Tooltip content={t.workspaceFiles.toggle}>
      <Button
        variant="ghost"
        className="text-muted-foreground hover:text-foreground px-2"
        onClick={onToggle}
        data-testid="workspace-files-toggle"
      >
        {open ? <FolderOpenIcon /> : <FolderIcon />}
        <span className="hidden lg:inline">{t.workspaceFiles.title}</span>
      </Button>
    </Tooltip>
  );
}

/** localStorage key persisting the panel's open/closed state. */
export const WORKSPACE_FILES_PANEL_OPEN_KEY =
  "deer-flow.workspace-files-panel-open";

export function readWorkspaceFilesPanelOpen(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(WORKSPACE_FILES_PANEL_OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeWorkspaceFilesPanelOpen(open: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      WORKSPACE_FILES_PANEL_OPEN_KEY,
      open ? "1" : "0",
    );
  } catch {
    // Storage unavailable (private mode etc.) — persistence is best-effort.
  }
}

/**
 * Open/closed state of the workspace file panel, persisted in localStorage.
 *
 * Defaults to collapsed and only reads localStorage after mount (hydration
 * guard), so SSR and the first client render always agree.
 */
export function useWorkspaceFilesPanelOpen(): [boolean, () => void] {
  const [open, setOpen] = useState(false);
  const mountedRef = useRef(false);

  useEffect(() => {
    setOpen(readWorkspaceFilesPanelOpen());
    mountedRef.current = true;
  }, []);

  const toggle = useCallback(() => {
    setOpen((previous) => {
      const next = !previous;
      if (mountedRef.current) writeWorkspaceFilesPanelOpen(next);
      return next;
    });
  }, []);

  return [open, toggle];
}
