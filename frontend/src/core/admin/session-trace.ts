export type TraceMode = "user" | "exact" | "all";

export function getTraceMode(
  userId: string | null | undefined,
  threadId: string,
  runId: string,
): TraceMode {
  if (threadId.trim() || runId.trim()) return "exact";
  return userId ? "user" : "all";
}

export function shouldLoadTraceOverview(mode: TraceMode): boolean {
  return mode === "user";
}
