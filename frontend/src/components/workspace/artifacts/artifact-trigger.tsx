import { FilesIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/workspace/tooltip";
import { useI18n } from "@/core/i18n/hooks";

import { useArtifacts } from "./context";

/**
 * Single header entry point into the shared right-side workspace panel.
 *
 * The panel hosts two tabbed views — conversation artifacts and the thread
 * workspace file browser — so the trigger is useful even for a thread with no
 * artifacts (the file browser works on any thread) and renders unconditionally.
 * Opening it shows the last-used tab; switching tabs lives inside the panel.
 */
export const ArtifactTrigger = () => {
  const { t } = useI18n();
  const { setOpen: setArtifactsOpen } = useArtifacts();

  return (
    <Tooltip content={t.workspaceFiles.toggle}>
      <Button
        className="text-muted-foreground hover:text-foreground"
        variant="ghost"
        onClick={() => {
          setArtifactsOpen(true);
        }}
      >
        <FilesIcon />
        {t.common.artifacts}
      </Button>
    </Tooltip>
  );
};
