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
