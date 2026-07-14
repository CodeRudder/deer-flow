import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  QuotaPeriod,
  QuotaStatus,
  QuotaUpdatePayload,
  QuotaUser,
  QuotaUsersResponse,
  SessionMetric,
  TraceEventsResponse,
  TraceRunsResponse,
  TraceUserOverview,
  TraceUsersResponse,
  UsageModels,
  UsageRange,
  UsageSessions,
  UsageSummary,
  UsageTrends,
  UsageUsers,
} from "./types";

function adminUrl(
  path: string,
  params?: Record<string, string | number | undefined>,
): string {
  const search = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  });
  const query = search.toString();
  return `${getBackendBaseURL()}${path}${query ? `?${query}` : ""}`;
}

async function readOrThrow<T>(
  response: Response,
  fallback: string,
): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  const body = (await response.json().catch(() => ({}))) as {
    detail?: unknown;
  };
  const detail = body.detail;
  if (typeof detail === "string") throw new Error(detail);
  if (detail && typeof detail === "object" && "message" in detail) {
    throw new Error(String((detail as { message?: unknown }).message));
  }
  throw new Error(fallback);
}

export function loadUsageSummary(params: {
  range: UsageRange;
  start?: string;
  end?: string;
}) {
  return fetch(adminUrl("/api/admin/usage/summary", params)).then((response) =>
    readOrThrow<UsageSummary>(response, "Failed to load usage summary"),
  );
}

export function loadUsageTrends(params: {
  range: UsageRange;
  start?: string;
  end?: string;
}) {
  return fetch(adminUrl("/api/admin/usage/trends", params)).then((response) =>
    readOrThrow<UsageTrends>(response, "Failed to load usage trends"),
  );
}

export function loadUsageSessions(params: {
  range: UsageRange;
  metric: SessionMetric;
  start?: string;
  end?: string;
}) {
  return fetch(adminUrl("/api/admin/usage/sessions", params)).then((response) =>
    readOrThrow<UsageSessions>(response, "Failed to load session usage"),
  );
}

export function loadUsageUsers(params: {
  range: UsageRange;
  limit?: number;
  start?: string;
  end?: string;
}) {
  return fetch(adminUrl("/api/admin/usage/users", params)).then((response) =>
    readOrThrow<UsageUsers>(response, "Failed to load user usage"),
  );
}

export function loadUsageModels(params: {
  range: UsageRange;
  start?: string;
  end?: string;
}) {
  return fetch(adminUrl("/api/admin/usage/models", params)).then((response) =>
    readOrThrow<UsageModels>(response, "Failed to load model usage"),
  );
}

export function loadQuotaUsers(params: {
  period: QuotaPeriod;
  period_start?: string;
  status: QuotaStatus;
  keyword?: string;
  page?: number;
  page_size?: number;
}) {
  return fetch(adminUrl("/api/admin/quotas/users", params)).then((response) =>
    readOrThrow<QuotaUsersResponse>(response, "Failed to load quota users"),
  );
}

export function updateQuotaUser(userId: string, payload: QuotaUpdatePayload) {
  return fetch(
    adminUrl(`/api/admin/quotas/users/${encodeURIComponent(userId)}`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  ).then((response) =>
    readOrThrow<QuotaUser>(response, "Failed to update quota"),
  );
}

export function loadTraceUsers(params: {
  keyword?: string;
  limit?: number;
  cursor?: string;
}) {
  return fetch(adminUrl("/api/admin/session-traces/users", params)).then(
    (response) =>
      readOrThrow<TraceUsersResponse>(response, "Failed to load trace users"),
  );
}

export function loadTraceUserOverview(userId: string) {
  return fetch(
    adminUrl(
      `/api/admin/session-traces/users/${encodeURIComponent(userId)}/overview`,
    ),
  ).then((response) =>
    readOrThrow<TraceUserOverview>(response, "Failed to load user overview"),
  );
}

export function loadTraceRuns(params: {
  user_id?: string;
  thread_id?: string;
  run_id?: string;
  page?: number;
  page_size?: number;
}) {
  return fetch(adminUrl("/api/admin/session-traces/runs", params)).then(
    (response) =>
      readOrThrow<TraceRunsResponse>(response, "Failed to load trace runs"),
  );
}

export function loadTraceEvents(runId: string, threadId: string, limit = 500) {
  return fetch(
    adminUrl(
      `/api/admin/session-traces/runs/${encodeURIComponent(runId)}/events`,
      {
        thread_id: threadId,
        limit,
      },
    ),
  ).then((response) =>
    readOrThrow<TraceEventsResponse>(response, "Failed to load run events"),
  );
}
