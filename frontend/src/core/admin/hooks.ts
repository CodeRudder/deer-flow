import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  loadQuotaUsers,
  loadTraceEvents,
  loadTraceRuns,
  loadTraceUserOverview,
  loadTraceUsers,
  loadUsageModels,
  loadUsageSessions,
  loadUsageSummary,
  loadUsageTrends,
  updateQuotaUser,
} from "./api";
import type {
  QuotaPeriod,
  QuotaStatus,
  QuotaUpdatePayload,
  SessionMetric,
  UsageRange,
} from "./types";

export function useAdminUsage(
  range: UsageRange,
  metric: SessionMetric,
  custom?: { start?: string; end?: string },
) {
  const rangeParams = { range, start: custom?.start, end: custom?.end };
  const enabled = range !== "custom" || Boolean(custom?.start && custom?.end);
  const summary = useQuery({
    queryKey: ["admin", "usage", "summary", rangeParams],
    queryFn: () => loadUsageSummary(rangeParams),
    enabled,
  });
  const trends = useQuery({
    queryKey: ["admin", "usage", "trends", rangeParams],
    queryFn: () => loadUsageTrends(rangeParams),
    enabled,
  });
  const sessions = useQuery({
    queryKey: ["admin", "usage", "sessions", rangeParams, metric],
    queryFn: () => loadUsageSessions({ ...rangeParams, metric }),
    enabled,
  });
  const models = useQuery({
    queryKey: ["admin", "usage", "models", rangeParams],
    queryFn: () => loadUsageModels(rangeParams),
    enabled,
  });
  return { summary, trends, sessions, models };
}

export function useQuotaUsers(params: {
  period: QuotaPeriod;
  period_start?: string;
  status: QuotaStatus;
  keyword?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery({
    queryKey: ["admin", "quotas", "users", params],
    queryFn: () =>
      loadQuotaUsers({
        ...params,
        page: params.page ?? 1,
        page_size: params.page_size ?? 50,
      }),
  });
}

export function useUpdateQuotaUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      userId,
      payload,
    }: {
      userId: string;
      payload: QuotaUpdatePayload;
    }) => updateQuotaUser(userId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "quotas"] });
    },
  });
}

export function useTraceUsers(keyword: string) {
  return useQuery({
    queryKey: ["admin", "session-traces", "users", keyword],
    queryFn: () => loadTraceUsers({ keyword: keyword || undefined, limit: 20 }),
  });
}

export function useTraceUserOverview(
  userId: string | null,
  exactMode: boolean,
) {
  return useQuery({
    queryKey: ["admin", "session-traces", "overview", userId],
    queryFn: () => loadTraceUserOverview(userId!),
    enabled: Boolean(userId) && !exactMode,
  });
}

export function useTraceRuns(params: {
  user_id?: string;
  thread_id?: string;
  run_id?: string;
  page: number;
  page_size: number;
}) {
  return useQuery({
    queryKey: ["admin", "session-traces", "runs", params],
    queryFn: () => loadTraceRuns(params),
  });
}

export function useTraceEvents(
  runId: string | null,
  threadId: string | null,
  open: boolean,
) {
  return useQuery({
    queryKey: ["admin", "session-traces", "events", threadId, runId],
    queryFn: () => loadTraceEvents(runId!, threadId!),
    enabled: open && Boolean(runId && threadId),
  });
}
