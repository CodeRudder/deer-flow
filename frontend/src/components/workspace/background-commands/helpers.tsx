import {
  CheckCircleIcon,
  Loader2Icon,
  SquareIcon,
  TerminalIcon,
  XCircleIcon,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import type { CommandStatus } from "./types";

export function RelativeTime({ date }: { date: string }) {
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

export function StatusIcon({ status }: { status: CommandStatus }): ReactNode {
  switch (status) {
    case "running":
      return <Loader2Icon className="size-4 animate-spin text-blue-500" />;
    case "completed":
      return <CheckCircleIcon className="size-4 text-green-500" />;
    case "failed":
    case "timed_out":
      return <XCircleIcon className="size-4 text-red-500" />;
    case "killed":
      return <SquareIcon className="size-4 text-muted-foreground" />;
    default:
      return <TerminalIcon className="size-4 text-muted-foreground" />;
  }
}

export function statusBadgeClass(status: CommandStatus): string {
  switch (status) {
    case "running":
      return "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400";
    case "completed":
      return "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400";
    case "failed":
    case "timed_out":
      return "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400";
    case "killed":
      return "bg-gray-100 text-gray-800 dark:bg-gray-900/30 dark:text-gray-400";
    default:
      return "bg-gray-100 text-gray-800 dark:bg-gray-900/30 dark:text-gray-400";
  }
}
