import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createQuotaScope,
  loadQuotaScopes,
  loadQuotaUserDetail,
  loadQuotaUsers,
  loadUserQuotaPeriods,
  loadTraceEvents,
  loadTraceRuns,
  loadTraceUserOverview,
  loadTraceUsers,
  loadUsageModels,
  loadUsageSummary,
  loadUsageTrends,
  loadUsageUsers,
  overrideUserCurrentPeriod,
  restoreUserCurrentPeriod,
  updateQuotaScope,
} from "./api";
import type {
  QuotaOverridePayload,
  QuotaScopePayload,
  QuotaStatus,
  UsageRange,
} from "./types";

export function useAdminUsage(
  range: UsageRange,
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
  const users = useQuery({
    queryKey: ["admin", "usage", "users", rangeParams],
    queryFn: () => loadUsageUsers({ ...rangeParams, limit: 20 }),
    enabled,
  });
  const models = useQuery({
    queryKey: ["admin", "usage", "models", rangeParams],
    queryFn: () => loadUsageModels(rangeParams),
    enabled,
  });
  return { summary, trends, users, models };
}

export function useQuotaUsers(params: {
  at?: string;
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

export function useQuotaScopes(includeDisabled = true) {
  return useQuery({
    queryKey: ["admin", "quotas", "scopes", includeDisabled],
    queryFn: () => loadQuotaScopes({ include_disabled: includeDisabled }),
  });
}

export function useQuotaUserDetail(userId: string | null, at?: string) {
  return useQuery({
    queryKey: ["admin", "quotas", "users", userId, "detail", at],
    queryFn: () => loadQuotaUserDetail(userId!, at),
    enabled: Boolean(userId),
  });
}

export function useSaveQuotaScope() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      scopeId,
      payload,
    }: {
      scopeId?: string;
      payload: QuotaScopePayload;
    }) =>
      scopeId ? updateQuotaScope(scopeId, payload) : createQuotaScope(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "quotas"] });
    },
  });
}

export function useOverrideUserCurrentPeriod() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      userId,
      scopeId,
      payload,
    }: {
      userId: string;
      scopeId: string;
      payload: QuotaOverridePayload;
    }) => overrideUserCurrentPeriod(userId, scopeId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "quotas"] });
    },
  });
}

export function useRestoreUserCurrentPeriod() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, scopeId }: { userId: string; scopeId: string }) =>
      restoreUserCurrentPeriod(userId, scopeId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "quotas"] });
    },
  });
}

export function useUserQuotaPeriods(
  userId: string | null,
  scopeId: string | null,
  params?: { start?: string; end?: string; limit?: number; cursor?: string },
) {
  return useQuery({
    queryKey: [
      "admin",
      "quotas",
      "users",
      userId,
      "scopes",
      scopeId,
      "periods",
      params,
    ],
    queryFn: () => loadUserQuotaPeriods(userId!, scopeId!, params),
    enabled: Boolean(userId && scopeId),
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
