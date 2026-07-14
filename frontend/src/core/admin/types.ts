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

export interface UsageUserRank {
  rank: number;
  user_id: string;
  email: string;
  value: number;
}

export interface UsageUsers {
  period: PeriodInfo;
  rankings: {
    tokens: UsageUserRank[];
    requests: UsageUserRank[];
    images: UsageUserRank[];
  };
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

export interface TraceUserOption {
  user_id: string;
  email: string;
  last_active_at: string | null;
}

export interface TraceUsersResponse {
  items: TraceUserOption[];
  next_cursor: string | null;
}

export interface TraceUserOverview {
  user: TraceUserOption;
  period: {
    days: number;
    period_start: string;
    period_end: string;
    timezone: string;
  };
  summary: {
    thread_count: number;
    run_count: number;
    total_tokens: number;
    model_requests: number;
    image_generations: number;
  };
  trends: Array<{
    date: string;
    tokens: number;
    model_requests: number;
    image_generations: number;
  }>;
  models: Array<{ model: string; tokens: number; share: number }>;
}

export interface TraceRun {
  run_id: string;
  thread_id: string;
  thread_title: string;
  user_id: string | null;
  email: string | null;
  status: string;
  model_name: string | null;
  created_at: string | null;
  updated_at: string | null;
  duration_ms: number | null;
  duration_approximate: boolean;
  first_human_message: string | null;
  last_ai_message: string | null;
  input_preview: string;
  output_preview: string;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  llm_call_count: number;
  image_generation_count: number;
  token_usage_by_model: Record<string, unknown>;
  error: string | null;
}

export interface TraceRunsResponse {
  items: TraceRun[];
  total: number;
  page: number;
  page_size: number;
}

export interface TraceEvent {
  thread_id: string;
  run_id: string;
  event_type: string;
  category: string;
  content: unknown;
  metadata: Record<string, unknown>;
  seq: number;
  created_at: string;
}

export interface TraceEventsResponse {
  run_id: string;
  thread_id: string;
  items: TraceEvent[];
  returned: number;
  limit: number;
  truncated: boolean;
  tool_summary: {
    total_calls: number;
    tools: Array<{ name: string; call_count: number }>;
    complete: boolean;
  };
}
