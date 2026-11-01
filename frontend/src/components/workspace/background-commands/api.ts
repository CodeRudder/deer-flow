import { getBackendBaseURL } from "@/core/config";

import type { BackgroundCommand, CommandOutput, KillResult } from "./types";

export async function fetchCommands(
  threadId: string,
): Promise<BackgroundCommand[]> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/threads/${threadId}/commands`,
  );
  if (!res.ok) return [];
  const data = await res.json();
  return data.commands ?? [];
}

export async function fetchOutput(
  threadId: string,
  commandId: string,
  startLine?: number,
  lineCount = 20,
): Promise<CommandOutput | null> {
  const params = new URLSearchParams();
  if (startLine !== undefined) params.set("start_line", String(startLine));
  params.set("line_count", String(lineCount));
  const res = await fetch(
    `${getBackendBaseURL()}/api/threads/${threadId}/commands/${commandId}/output?${params}`,
  );
  if (!res.ok) return null;
  return res.json();
}

export async function killCommand(
  threadId: string,
  commandId: string,
): Promise<KillResult> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/threads/${threadId}/commands/${commandId}/kill`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error("Failed to kill command");
  return res.json();
}
