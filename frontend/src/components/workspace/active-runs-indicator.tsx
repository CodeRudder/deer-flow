"use client";

import { LoaderIcon, SquareIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { getBackendBaseURL } from "@/core/config";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ActiveRun = {
  run_id: string;
  thread_id: string;
  status: string;
  created_at: string;
};

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchActiveRuns(): Promise<ActiveRun[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/runs/active`);
  if (!res.ok) return [];
  return res.json();
}

async function cancelAllRuns(): Promise<{
  cancelled: string[];
  failed: string[];
  total: number;
}> {
  const res = await fetch(`${getBackendBaseURL()}/api/runs/cancel-all`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to cancel runs");
  return res.json();
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function RelativeTime({ date }: { date: string }) {
  const [text, setText] = useState("");

  useEffect(() => {
    function calc() {
      const now = Date.now();
      const then = new Date(date).getTime();
      const diff = Math.max(0, now - then);
      const secs = Math.floor(diff / 1000);
      if (secs < 60) setText(`${secs}s ago`);
      else if (secs < 3600) setText(`${Math.floor(secs / 60)}m ago`);
      else setText(`${Math.floor(secs / 3600)}h ago`);
    }
    calc();
    const id = setInterval(calc, 10000);
    return () => clearInterval(id);
  }, [date]);

  return <>{text}</>;
}

function RunItem({ run }: { run: ActiveRun }) {
  const shortId = run.thread_id.slice(0, 8);
  return (
    <div className="hover:bg-muted/50 flex items-center gap-3 rounded-md px-3 py-2">
      <LoaderIcon className="text-muted-foreground h-4 w-4 animate-spin" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">Thread {shortId}...</p>
        <p className="text-muted-foreground text-xs">
          <RelativeTime date={run.created_at} />
        </p>
      </div>
      <span className="rounded bg-yellow-100 px-1.5 py-0.5 text-xs text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400">
        {run.status}
      </span>
    </div>
  );
}

export function ActiveRunsIndicator() {
  const [runs, setRuns] = useState<ActiveRun[]>([]);
  const [open, setOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchActiveRuns();
      setRuns(data);
    } catch {
      // silently ignore
    }
  }, []);

  // Poll every 3 seconds
  useEffect(() => {
    void refresh();
    const id = setInterval(() => {
      void refresh();
    }, 3000);
    return () => clearInterval(id);
  }, [refresh]);

  const handleCancelAll = useCallback(async () => {
    setCancelling(true);
    try {
      const result = await cancelAllRuns();
      toast.success(
        `Stopped ${result.cancelled.length} run(s)${result.failed.length > 0 ? `, ${result.failed.length} failed` : ""}`,
      );
      await refresh();
    } catch {
      toast.error("Failed to stop runs");
    } finally {
      setCancelling(false);
    }
  }, [refresh]);

  if (runs.length === 0) return null;

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="outline"
          size="icon"
          className="border-primary/30 bg-background hover:bg-accent fixed right-4 bottom-4 z-50 h-12 w-12 rounded-full border-2 shadow-lg"
        >
          <LoaderIcon className="h-5 w-5 animate-spin" />
          <span className="bg-primary text-primary-foreground absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold">
            {runs.length}
          </span>
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-80 sm:w-96">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <LoaderIcon className="h-4 w-4 animate-spin" />
            Active Runs ({runs.length})
          </SheetTitle>
          <SheetDescription className="sr-only">
            Review active LangGraph runs and stop running tasks.
          </SheetDescription>
        </SheetHeader>
        <div className="mt-4 flex flex-col gap-2">
          <Button
            variant="destructive"
            size="sm"
            className="w-full"
            disabled={cancelling}
            onClick={() => setConfirmOpen(true)}
          >
            {cancelling ? (
              <LoaderIcon className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <SquareIcon className="mr-2 h-4 w-4" />
            )}
            Stop All Tasks
          </Button>
          <div className="mt-2 flex max-h-[60vh] flex-col gap-1 overflow-y-auto">
            {runs.map((run) => (
              <RunItem key={run.run_id} run={run} />
            ))}
          </div>
        </div>
      </SheetContent>
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>停止所有任务</AlertDialogTitle>
            <AlertDialogDescription>
              确定要停止所有 {runs.length}{" "}
              个运行中的任务吗？正在执行的工作将丢失。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90 text-white"
              onClick={() => void handleCancelAll()}
            >
              确认停止
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Sheet>
  );
}
