import { useQuery } from "@tanstack/react-query";

import { getBackendBaseURL } from "../config";

const STATUS_POLL_INTERVAL_MS = 10000;
// interrupted is treated as terminal: current backend semantics require user
// action to cancel/resume it, and those actions trigger an explicit refresh.
const TERMINAL_SUBTASK_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
  "timed_out",
]);

export interface SubagentMessage {
  ts: string;
  role: "human" | "ai" | "tool";
  content:
    | string
    | Array<{
        type?: string;
        text?: string;
        image_url?: string | { url?: string };
      }>;
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

function hasActiveSubtasks(data: SubagentSessionSummary[] | undefined) {
  return (
    data?.some((item) => !TERMINAL_SUBTASK_STATUSES.has(item.status)) ?? false
  );
}

export function useSubtaskMessages(threadId: string, taskId: string | null) {
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

/**
 * @param forcePolling Force STATUS_POLL_INTERVAL_MS polling, overriding data-adaptive pauses.
 * Still respects enabled: enabled=false disables the query and polling.
 */
export function useSubtaskStatuses(
  threadId: string,
  enabled = true,
  forcePolling = false,
) {
  return useQuery<SubagentSessionSummary[]>({
    queryKey: ["subagents-statuses", threadId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/threads/${threadId}/subagents`,
      );
      if (!res.ok) return [];
      return res.json();
    },
    enabled,
    refetchInterval: (query) => {
      if (!enabled) return false;
      if (forcePolling) return STATUS_POLL_INTERVAL_MS;
      const data = query.state.data;
      if (!data) return STATUS_POLL_INTERVAL_MS;
      return hasActiveSubtasks(data) ? STATUS_POLL_INTERVAL_MS : false;
    },
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

function hasActiveSessionWork(data: SessionStatus | undefined) {
  return (
    data?.main_session.status === "running" ||
    (data?.active_subtasks.length ?? 0) > 0
  );
}

/**
 * @param forcePolling Force STATUS_POLL_INTERVAL_MS polling, overriding data-adaptive pauses.
 * Still respects enabled: enabled=false disables the query and polling.
 */
export function useSessionStatus(
  threadId: string,
  enabled = true,
  forcePolling = false,
) {
  return useQuery<SessionStatus>({
    queryKey: ["session-status", threadId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/threads/${threadId}/status`,
      );
      if (!res.ok) throw new Error("Failed to fetch session status");
      return res.json();
    },
    enabled,
    refetchInterval: (query) => {
      if (!enabled) return false;
      if (forcePolling) return STATUS_POLL_INTERVAL_MS;
      const data = query.state.data;
      if (!data) return STATUS_POLL_INTERVAL_MS;
      return hasActiveSessionWork(data) ? STATUS_POLL_INTERVAL_MS : false;
    },
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

// ---------------------------------------------------------------------------
// Main session messages (from conversation.jsonl)
// ---------------------------------------------------------------------------

export interface ThreadMessagesResponse {
  messages: SubagentMessage[];
  total: number;
  has_more: boolean;
}

export function useMainSessionMessages(
  threadId: string,
  enabled: boolean,
  limit = 100,
  offset = 0,
) {
  return useQuery<ThreadMessagesResponse>({
    queryKey: ["thread-messages", threadId, limit, offset],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/threads/${threadId}/main-session/messages?limit=${limit}&offset=${offset}`,
      );
      if (!res.ok) throw new Error("Failed to fetch thread messages");
      return res.json();
    },
    enabled,
  });
}
