import type {
  AccountStatus,
  ApprovalEmailStatus,
} from "@/core/auth/user-status";

export {
  ACCOUNT_STATUS,
  APPROVAL_EMAIL_STATUS,
  USER_STATUS_FILTER,
} from "@/core/auth/user-status";
export type {
  AccountStatus,
  ApprovalEmailStatus,
  UserStatusFilter,
} from "@/core/auth/user-status";

export type UsageRange = "day" | "week" | "month" | "custom";
export type UserRole = "admin" | "user";
export type UserSource = "local" | "oidc" | "platform";
export type AdminUserAction =
  | "edit"
  | "approve"
  | "disable"
  | "enable"
  | "retry_approval_email";

export interface AdminUserSummary {
  registration_approval_enabled: boolean;
  total: number;
  active: number;
  pending: number;
  disabled: number;
}

export interface AdminUser {
  id: string;
  email: string;
  role: UserRole;
  source: UserSource;
  source_provider: string | null;
  created_at: string;
  account_status: AccountStatus;
  registration_requested_at: string | null;
  registration_approved_at: string | null;
  registration_approved_by: string | null;
  approval_email_status: ApprovalEmailStatus | null;
  approval_email_last_attempt_at: string | null;
  allowed_actions: AdminUserAction[];
}

export interface AdminUsersResponse {
  items: AdminUser[];
  total: number;
  page: number;
  page_size: number;
}

export type QuotaStatus =
  | "all"
  | "normal"
  | "warning"
  | "exceeded"
  | "unlimited";
export type SessionMetric = "tokens" | "requests" | "images" | "videos";

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
  video_generations: number;
  run_count: number;
  active_users: number;
  running_runs: number;
}

export interface UsageTrendPoint {
  label: string;
  tokens: number;
  requests: number;
  images: number;
  videos: number;
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
    videos: UsageUserRank[];
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
  enforced: boolean;
  used: number;
  limit: number | null;
  remaining: number | null;
  ratio: number | null;
  status: Exclude<QuotaStatus, "all">;
  /** Video points metrics expose reservations and their display unit. */
  reserved?: number;
  billing_mode?: "points";
  unit?: "points";
  scale?: number;
  /** Per-day usage metric, present only when a daily cap is configured. */
  daily?: QuotaDailyMetric | null;
}

export interface QuotaDailyMetric {
  enforced: boolean;
  used: number;
  limit: number | null;
  remaining: number | null;
  ratio: number | null;
  status: Exclude<QuotaStatus, "all">;
  reserved?: number;
  period: { period_type: "daily"; label: string; timezone: string };
}

export interface QuotaMetricPolicy {
  enforced: boolean;
  limit: number | null;
  /** Optional per-day cap riding on this metric; absent means unconfigured. */
  daily?: QuotaDailyMetricPolicy | null;
}

export interface QuotaDailyMetricPolicy {
  enforced: boolean;
  limit: number | null;
}

export interface QuotaVideoMetricPolicy extends QuotaMetricPolicy {
  billing_mode?: "points";
  billing_rules?: Record<string, unknown>;
}

export interface QuotaScopePolicy {
  period_type: "weekly" | "monthly";
  requests: QuotaMetricPolicy | null;
  images: QuotaMetricPolicy | null;
  videos: QuotaVideoMetricPolicy | null;
  policy_version: number;
}

export interface QuotaScope {
  id: string;
  code: string;
  name: string;
  resource_type: "model" | "image_generation" | "video_generation";
  match_rules: { exact: string[]; prefix: string[] };
  matched_models: Array<{
    name: string;
    display_name: string;
    model: string;
  }>;
  enabled: boolean;
  is_system: boolean;
  default_policy: QuotaScopePolicy;
  video_billing_rules?: Record<string, unknown>;
  current_period_user_count: number;
  updated_at: string;
  updated_by: string | null;
}

export interface QuotaScopesResponse {
  items: QuotaScope[];
  unmatched_models: Array<{
    name: string;
    display_name: string;
    model: string;
  }>;
  configuration_warnings: Array<{
    code: string;
    model?: string;
  }>;
}

export interface UserQuotaItem {
  usage_period_id: string | null;
  source: "scope_default" | "temporary_override";
  scope: Pick<
    QuotaScope,
    "id" | "code" | "name" | "resource_type" | "is_system"
  >;
  period_type: "weekly" | "monthly";
  period: {
    period_type: "weekly" | "monthly";
    label: string;
    period_start: string;
    period_end: string;
    timezone: string;
  };
  token_observation: { used: number } | null;
  requests: QuotaMetric | null;
  images: QuotaMetric | null;
  videos: QuotaMetric | null;
  scope_policy_version: number;
  temporary_override: {
    overridden_at: string | null;
    overridden_by: string | null;
    reason: string | null;
  } | null;
  status: Exclude<QuotaStatus, "all">;
  updated_at: string;
  updated_by: string | null;
}

export interface QuotaUser {
  user_id: string;
  email: string;
  role: string;
  status: Exclude<QuotaStatus, "all">;
  image_generation: UserQuotaItem | null;
  video_generation: UserQuotaItem | null;
  model_groups: {
    enabled: number;
    warning: number;
    exceeded: number;
    overridden: number;
    items: QuotaUserModelGroupItem[];
  };
}

export interface QuotaUserModelGroupItem {
  scope_id: string;
  name: string;
  period_type: "weekly" | "monthly";
  period: UserQuotaItem["period"];
  source: "scope_default" | "temporary_override";
  status: Exclude<QuotaStatus, "all">;
  requests: QuotaMetric;
  token_observation: { used: number };
}

export interface QuotaUsersResponse {
  items: QuotaUser[];
  total: number;
  page: number;
  page_size: number;
  reference_at: string;
  timezone: string;
}

export interface QuotaUserDetail {
  user: { user_id: string; email: string; role: string };
  reference_at: string;
  timezone: string;
  status: Exclude<QuotaStatus, "all">;
  items: UserQuotaItem[];
}

export interface QuotaPeriodsResponse {
  user_id: string;
  scope: Pick<QuotaScope, "id" | "code" | "name">;
  items: UserQuotaItem[];
  next_cursor: string | null;
}

export interface QuotaScopePayload {
  code?: string;
  name: string;
  resource_type?: "model" | "image_generation" | "video_generation";
  match_rules: { exact: string[]; prefix: string[] };
  default_policy: {
    period_type: "weekly" | "monthly";
    requests: QuotaMetricPolicy | null;
    images: QuotaMetricPolicy | null;
    videos: QuotaVideoMetricPolicy | null;
  };
  enabled: boolean;
}

export interface QuotaOverridePayload {
  requests?: QuotaMetricPolicy | null;
  images?: QuotaMetricPolicy | null;
  videos?: QuotaVideoMetricPolicy | null;
  reason?: string;
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
    video_generations: number;
  };
  trends: Array<{
    date: string;
    tokens: number;
    model_requests: number;
    image_generations: number;
    video_generations: number;
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
  video_generation_count: number;
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
