import { useQuery } from "@tanstack/react-query";

import { getBackendBaseURL } from "../config";

export interface SubagentMessage {
  ts: string;
  role: "human" | "ai" | "tool";
  content: string;
  id?: string;
  tool_calls?: Array<{
    id: string;
    name: string;
    args: Record<string, unknown>;
  }>;
  tool_call_id?: string;
  name?: string;
  reasoning?: string;
}

export interface SubagentSessionDetail {
  task_id: string;
  subagent_name: string;
  status: string;
  messages: SubagentMessage[];
}

export interface SubagentSessionSummary {
  task_id: string;
  subagent_name: string;
  description: string;
  status: string;
  started_at: string;
  completed_at: string;
  message_count: number;
}

export function useSubtaskMessages(
  threadId: string,
  taskId: string | null,
) {
  return useQuery<SubagentSessionDetail>({
    queryKey: ["subagents", threadId, taskId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/threads/${threadId}/subagents/${taskId}`,
      );
      if (!res.ok) throw new Error("Failed to fetch subagent session");
      return res.json();
    },
    enabled: !!taskId,
  });
}

export function useSubtaskStatuses(threadId: string) {
  return useQuery<SubagentSessionSummary[]>({
    queryKey: ["subagents-statuses", threadId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/threads/${threadId}/subagents`,
      );
      if (!res.ok) return [];
      return res.json();
    },
    refetchInterval: 10000,
  });
}

// ---------------------------------------------------------------------------
// Session status overview
// ---------------------------------------------------------------------------

export interface MainSessionStatus {
  status: string;
  run_id: string | null;
  started_at: string | null;
  last_updated: string | null;
  last_message: string | null;
}

export interface SubtaskStatusItem {
  task_id: string;
  subagent_name: string;
  description: string;
  status: string;
  detail: string;
  started_at: string | null;
  last_updated: string | null;
  last_message: string | null;
}

export interface SessionStatus {
  thread_id: string;
  main_session: MainSessionStatus;
  active_subtasks: SubtaskStatusItem[];
  recent_subtasks: SubtaskStatusItem[];
}

export function useSessionStatus(threadId: string) {
  return useQuery<SessionStatus>({
    queryKey: ["session-status", threadId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/threads/${threadId}/status`,
      );
      if (!res.ok) throw new Error("Failed to fetch session status");
      return res.json();
    },
    refetchInterval: 10000,
  });
}

export async function cancelSubtask(taskId: string): Promise<{
  task_id: string;
  cancelled: boolean;
  error: string | null;
}> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/runs/subtasks/${taskId}/cancel`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error("Failed to cancel subtask");
  return res.json();
}
