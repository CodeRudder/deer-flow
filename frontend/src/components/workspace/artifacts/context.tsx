import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { useSidebar } from "@/components/ui/sidebar";
import { env } from "@/env";

/**
 * Which view the right-side workspace panel shows.
 *
 * The panel is shared between the conversation artifacts and the thread
 * workspace file browser; the active tab persists across sessions.
 */
export type WorkspacePanelTab = "artifacts" | "files";

export const WORKSPACE_PANEL_TAB_KEY = "deer-flow.workspace-panel-tab";

function readPersistedPanelTab(): WorkspacePanelTab {
  if (typeof window === "undefined") return "artifacts";
  try {
    return window.localStorage.getItem(WORKSPACE_PANEL_TAB_KEY) === "files"
      ? "files"
      : "artifacts";
  } catch {
    return "artifacts";
  }
}

function persistPanelTab(tab: WorkspacePanelTab): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(WORKSPACE_PANEL_TAB_KEY, tab);
  } catch {
    // Storage unavailable (private mode etc.) — persistence is best-effort.
  }
}

export interface ArtifactsContextType {
  artifacts: string[];
  setArtifacts: (artifacts: string[]) => void;

  selectedArtifact: string | null;
  autoSelect: boolean;
  select: (artifact: string, autoSelect?: boolean) => void;
  deselect: () => void;

  open: boolean;
  autoOpen: boolean;
  setOpen: (open: boolean) => void;

  /** Active view of the shared right-side panel (persists in localStorage). */
  panelTab: WorkspacePanelTab;
  setPanelTab: (tab: WorkspacePanelTab) => void;
}

const ArtifactsContext = createContext<ArtifactsContextType | undefined>(
  undefined,
);

interface ArtifactsProviderProps {
  children: ReactNode;
  /**
   * Optional observers of the two entry points into the artifact UI.
   *
   * The desktop renders the selection in a resizable panel, so the state in
   * this context *is* the whole interaction. The mobile tree has no panel —
   * selecting an artifact is a route change — but it cannot re-provide this
   * context from the outside: it is not exported, and the two callers that
   * matter (`message-group.tsx`'s `write_file` steps, `ArtifactTrigger`) reach
   * for `useArtifacts()`. So the provider hands the same two moments out as
   * optional callbacks, and the mobile page passes navigation in from above.
   *
   * Both default to `undefined`, and neither is on any state path: when they
   * are absent the two functions below run exactly the statements they ran
   * before, in the same order. That is what keeps the desktop unchanged.
   */
  onSelect?: (artifact: string) => void;
  onOpenChange?: (open: boolean) => void;
}

export function ArtifactsProvider({
  children,
  onSelect,
  onOpenChange,
}: ArtifactsProviderProps) {
  const [artifacts, setArtifacts] = useState<string[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<string | null>(null);
  const [autoSelect, setAutoSelect] = useState(false);
  const [open, setOpen] = useState(
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
  );
  const [autoOpen, setAutoOpen] = useState(false);
  // Lazy so SSR renders the default and the persisted value only replaces it
  // on the client (no hydration mismatch).
  const [panelTab, setPanelTabState] = useState<WorkspacePanelTab>(
    () => "artifacts",
  );
  // Read the persisted tab once on the client after mount.
  const [panelTabHydrated, setPanelTabHydrated] = useState(false);
  const { setOpen: setSidebarOpen } = useSidebar();

  const setPanelTab = useCallback((tab: WorkspacePanelTab) => {
    setPanelTabState(tab);
    persistPanelTab(tab);
  }, []);

  const select = useCallback(
    (artifact: string, autoSelect = false) => {
      setSelectedArtifact(artifact);
      // An artifact selection is an artifacts-view action: make sure the
      // shared panel shows that tab even if the user last browsed files.
      setPanelTab("artifacts");
      if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true") {
        setSidebarOpen(false);
      }
      if (!autoSelect) {
        setAutoSelect(false);
      }
      onSelect?.(artifact);
    },
    [onSelect, setPanelTab, setSidebarOpen, setSelectedArtifact, setAutoSelect],
  );

  // Hydrate the persisted tab client-side after mount (SSR renders the
  // default, so there is no hydration mismatch).
  useEffect(() => {
    if (panelTabHydrated) return;
    setPanelTabHydrated(true);
    setPanelTabState(readPersistedPanelTab());
  }, [panelTabHydrated]);

  const deselect = useCallback(() => {
    setSelectedArtifact(null);
    setAutoSelect(true);
    setOpen(false);
    // `deselect` deliberately keeps using the raw setter: routing it through
    // `setOpen` below would additionally clear `autoOpen`/`autoSelect`, which
    // is a behaviour change for the desktop.
    onOpenChange?.(false);
  }, [onOpenChange]);

  const handleOpenChange = useCallback(
    (isOpen: boolean) => {
      if (!isOpen && autoOpen) {
        setAutoOpen(false);
        setAutoSelect(false);
      }
      setOpen(isOpen);
      onOpenChange?.(isOpen);
    },
    [autoOpen, onOpenChange],
  );

  const value: ArtifactsContextType = {
    artifacts,
    setArtifacts,

    open,
    autoOpen,
    autoSelect,
    setOpen: handleOpenChange,

    selectedArtifact,
    select,
    deselect,

    panelTab,
    setPanelTab,
  };

  return (
    <ArtifactsContext.Provider value={value}>
      {children}
    </ArtifactsContext.Provider>
  );
}

export function useArtifacts() {
  const context = useContext(ArtifactsContext);
  if (context === undefined) {
    throw new Error("useArtifacts must be used within an ArtifactsProvider");
  }
  return context;
}
