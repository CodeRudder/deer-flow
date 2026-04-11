"use client";

import { SquareIcon, TerminalIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

import { fetchCommands, killCommand } from "./background-commands/api";
import { CommandCard } from "./background-commands/command-card";
import type { BackgroundCommand } from "./background-commands/types";

// ---------------------------------------------------------------------------
// Main Indicator Component
// ---------------------------------------------------------------------------

export function BackgroundCommandsIndicator({
  threadId,
}: {
  threadId: string;
}) {
  const [commands, setCommands] = useState<BackgroundCommand[]>([]);
  const [open, setOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchCommands(threadId);
      setCommands(data);
    } catch {
      // silently ignore
    }
  }, [threadId]);

  // Poll every 5 seconds
  useEffect(() => {
    void refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const runningCount = commands.filter((c) => c.status === "running").length;

  const handleKillAll = useCallback(async () => {
    const running = commands.filter((c) => c.status === "running");
    let killed = 0;
    let failed = 0;
    for (const cmd of running) {
      try {
        const result = await killCommand(threadId, cmd.command_id);
        if (result.killed) killed++;
        else failed++;
      } catch {
        failed++;
      }
    }
    toast.success(
      `Stopped ${killed} command(s)${failed > 0 ? `, ${failed} failed` : ""}`,
    );
    await refresh();
  }, [threadId, commands, refresh]);

  if (commands.length === 0) return null;

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="outline"
          size="icon"
          className="fixed bottom-4 right-20 z-50 h-12 w-12 rounded-full shadow-lg border-2 border-primary/30 bg-background hover:bg-accent"
        >
          <TerminalIcon className="size-5" />
          {runningCount > 0 && (
            <span className="absolute -top-1 -right-1 flex size-5 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-primary-foreground">
              {runningCount}
            </span>
          )}
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-80 sm:w-96">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <TerminalIcon className="size-4" />
            Background Commands ({commands.length})
          </SheetTitle>
        </SheetHeader>
        <div className="mt-4 flex flex-col gap-2">
          {runningCount > 0 && (
            <Button
              variant="destructive"
              size="sm"
              className="w-full"
              onClick={() => void handleKillAll()}
            >
              <SquareIcon className="mr-2 size-4" />
              Stop All Running ({runningCount})
            </Button>
          )}
          <div className="mt-2 flex max-h-[60vh] flex-col gap-2 overflow-y-auto">
            {commands.map((cmd) => (
              <CommandCard
                key={cmd.command_id}
                command={cmd}
                threadId={threadId}
                onKilled={() => void refresh()}
              />
            ))}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
