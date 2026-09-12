import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";

import { useSidebar } from "@/components/ui/sidebar";
import { env } from "@/env";

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
  const { setOpen: setSidebarOpen } = useSidebar();

  const select = useCallback(
    (artifact: string, autoSelect = false) => {
      setSelectedArtifact(artifact);
      if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true") {
        setSidebarOpen(false);
      }
      if (!autoSelect) {
        setAutoSelect(false);
      }
      onSelect?.(artifact);
    },
    [onSelect, setSidebarOpen, setSelectedArtifact, setAutoSelect],
  );

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
