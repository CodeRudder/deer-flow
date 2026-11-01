import { z } from "zod";

import {
  ACCOUNT_STATUS,
  ACCOUNT_STATUS_VALUES,
  type AccountStatus,
} from "./user-status";

// ── User schema (single source of truth) ──────────────────────────

export const userSchema = z.object({
  id: z.string(),
  email: z.string().email(),
  system_role: z.enum(["admin", "user"]),
  needs_setup: z.boolean().optional().default(false),
  oauth_provider: z.string().nullable().optional().default(null),
  account_status: z
    .enum(ACCOUNT_STATUS_VALUES)
    .optional()
    .default(ACCOUNT_STATUS.ACTIVE),
});

export type User = Omit<
  z.infer<typeof userSchema>,
  "oauth_provider" | "account_status"
> & {
  oauth_provider?: string | null;
  account_status?: AccountStatus;
};

export const registrationPendingResponseSchema = z.object({
  status: z.literal(ACCOUNT_STATUS.PENDING),
  message: z.string(),
});

export type RegistrationPendingResponse = z.infer<
  typeof registrationPendingResponseSchema
>;

// ── SSR auth result (tagged union) ────────────────────────────────

export type AuthResult =
  | { tag: "authenticated"; user: User }
  | { tag: "needs_setup"; user: User }
  | { tag: "system_setup_required" }
  | { tag: "unauthenticated" }
  | { tag: "gateway_unavailable" }
  | { tag: "config_error"; message: string };

export function assertNever(x: never): never {
  throw new Error(`Unexpected auth result: ${JSON.stringify(x)}`);
}

export function buildLoginUrl(returnPath: string): string {
  return `/login?next=${encodeURIComponent(returnPath)}`;
}

// ── Backend error response parsing ────────────────────────────────

const AUTH_ERROR_CODES = [
  "invalid_credentials",
  "email_domain_not_allowed",
  "token_expired",
  "token_invalid",
  "user_not_found",
  "email_already_exists",
  "provider_not_found",
  "not_authenticated",
  "system_already_initialized",
  "registration_pending",
  "account_disabled",
] as const;

export type AuthErrorCode = (typeof AUTH_ERROR_CODES)[number];

export interface AuthErrorResponse {
  code: AuthErrorCode;
  message: string;
}

const AuthErrorSchema = z.object({
  code: z.enum(AUTH_ERROR_CODES),
  message: z.string(),
});

const ErrorDetailSchema = z.object({
  msg: z.string(),
  type: z.enum(["value_error"]),
  loc: z.array(z.string()),
});

export function parseAuthError(data: unknown): AuthErrorResponse {
  // Try top-level {code, message} first
  const parsed = AuthErrorSchema.safeParse(data);
  if (parsed.success) return parsed.data;

  // Unwrap FastAPI's {detail: {code, message}} envelope
  if (typeof data === "object" && data !== null && "detail" in data) {
    const detail = (data as Record<string, unknown>).detail;
    const nested = AuthErrorSchema.safeParse(detail);
    if (nested.success) return nested.data;
    // Legacy string-detail responses
    if (typeof detail === "string") {
      return { code: "invalid_credentials", message: detail };
    } else if (Array.isArray(detail)) {
      // Handle list of error details (e.g. from Pydantic validation)
      const firstDetail = detail[0];
      if (typeof firstDetail === "object" && firstDetail !== null) {
        const errorDetail = ErrorDetailSchema.safeParse(firstDetail);
        if (errorDetail.success) {
          return { code: "invalid_credentials", message: errorDetail.data.msg };
        }
      }
    } else if (typeof detail === "object" && detail !== null) {
      const errorDetail = ErrorDetailSchema.safeParse(detail);
      if (errorDetail.success) {
        return { code: "invalid_credentials", message: errorDetail.data.msg };
      }
    }
  }

  return { code: "invalid_credentials", message: "Authentication failed" };
}
