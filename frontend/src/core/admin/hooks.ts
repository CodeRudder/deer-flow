import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  loadQuotaUsers,
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
