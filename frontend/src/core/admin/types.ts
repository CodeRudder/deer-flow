export type UsageRange = "day" | "week" | "month" | "custom";
export type QuotaPeriod = "this_month" | "last_month" | "custom";
export type QuotaStatus =
  | "all"
  | "normal"
  | "warning"
  | "exceeded"
  | "unlimited";
export type SessionMetric = "tokens" | "requests" | "images";

export interface PeriodInfo {
  period: string;
  label: string;
  period_start: string;
  period_end: string;
  timezone: string;
}

export interface UsageSummary {
  period: PeriodInfo;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  model_requests: number;
  image_generations: number;
  run_count: number;
  active_users: number;
  running_runs: number;
}

export interface UsageTrendPoint {
  label: string;
  tokens: number;
  requests: number;
  images: number;
}

export interface UsageTrends {
  period: PeriodInfo;
  bucket: "hour" | "day";
  items: UsageTrendPoint[];
}

export interface UsageSession {
  thread_id: string;
  user_id: string | null;
  email: string;
  title: string;
  value: number;
}

export interface UsageSessions {
  period: PeriodInfo;
  metric: SessionMetric;
  items: UsageSession[];
}

export interface UsageModel {
  model: string;
  type: "LLM";
  requests: number | null;
  tokens: number;
  runs: number;
  share: number;
}

export interface UsageModels {
  period: PeriodInfo;
  items: UsageModel[];
}

export interface QuotaMetric {
  enabled: boolean;
  used: number;
  limit: number | null;
  remaining: number | null;
}

export interface QuotaUser {
  user_id: string;
  email: string;
  role: string;
  status: Exclude<QuotaStatus, "all">;
  period: PeriodInfo;
  model_tokens: QuotaMetric;
  model_requests: QuotaMetric;
  image_generations: QuotaMetric;
}

export interface QuotaUsersResponse {
  items: QuotaUser[];
  total: number;
  page: number;
  page_size: number;
  period: PeriodInfo;
}

export interface QuotaUpdatePayload {
  period: QuotaPeriod;
  period_start?: string;
  model_tokens?: { enabled?: boolean; limit?: number | null };
  model_requests?: { enabled?: boolean; limit?: number | null };
  image_generations?: { enabled?: boolean; limit?: number | null };
}
