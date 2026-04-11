import { SquareIcon } from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

import { killCommand } from "./api";
import { CommandOutputView } from "./command-output-view";
import { RelativeTime, StatusIcon, statusBadgeClass } from "./helpers";
import type { BackgroundCommand } from "./types";

export function CommandCard({
  command,
  threadId,
  onKilled,
}: {
  command: BackgroundCommand;
  threadId: string;
  onKilled: () => void;
}) {
  const [killing, setKilling] = useState(false);
  const [showOutput, setShowOutput] = useState(false);
  const isRunning = command.status === "running";

  const handleKill = useCallback(async () => {
    setKilling(true);
    try {
      const result = await killCommand(threadId, command.command_id);
      if (result.killed) {
        toast.success("Command stopped");
      } else {
        toast.error(result.message);
      }
      onKilled();
    } catch {
      toast.error("Failed to stop command");
    } finally {
      setKilling(false);
    }
  }, [threadId, command.command_id, onKilled]);

  return (
    <div className="rounded-md border p-3">
      <div className="flex items-start gap-3">
        <StatusIcon status={command.status} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium">
              {command.description}
            </p>
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${statusBadgeClass(command.status)}`}
            >
              {command.status}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs font-mono text-muted-foreground">
            {command.command}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            <RelativeTime date={command.started_at} />
            {command.pid != null && (
              <span className="ml-2">PID: {command.pid}</span>
            )}
            {command.return_code != null && (
              <span className="ml-2">Exit: {command.return_code}</span>
            )}
          </p>
        </div>
        <div className="flex shrink-0 gap-1">
          {isRunning && (
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              disabled={killing}
              onClick={() => void handleKill()}
            >
              <SquareIcon className="size-3" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs"
            onClick={() => setShowOutput(!showOutput)}
          >
            {showOutput ? "Hide" : "Output"}
          </Button>
        </div>
      </div>
      {showOutput && (
        <div className="mt-3 border-t pt-3">
          <CommandOutputView
            threadId={threadId}
            commandId={command.command_id}
            status={command.status}
          />
        </div>
      )}
    </div>
  );
}
