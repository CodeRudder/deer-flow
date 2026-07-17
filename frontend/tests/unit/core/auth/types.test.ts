import { describe, expect, it } from "@rstest/core";

import {
  parseAuthError,
  registrationPendingResponseSchema,
} from "@/core/auth/types";
import { ACCOUNT_STATUS, APPROVAL_EMAIL_STATUS } from "@/core/auth/user-status";

describe("user lifecycle status constants", () => {
  it("keeps account and approval-email wire values stable", () => {
    expect(Object.values(ACCOUNT_STATUS)).toEqual([
      "active",
      "pending",
      "disabled",
    ]);
    expect(Object.values(APPROVAL_EMAIL_STATUS)).toEqual([
      "pending",
      "sending",
      "sent",
      "failed",
    ]);
  });
});

describe("parseAuthError", () => {
  it("parses an email domain rejection from a FastAPI detail envelope", () => {
    expect(
      parseAuthError({
        detail: {
          code: "email_domain_not_allowed",
          message: "请使用公司邮箱注册",
        },
      }),
    ).toEqual({
      code: "email_domain_not_allowed",
      message: "请使用公司邮箱注册",
    });
  });

  it("falls back when the backend returns an unknown error code", () => {
    expect(
      parseAuthError({
        detail: {
          code: "future_auth_error",
          message: "A future error message",
        },
      }),
    ).toEqual({
      code: "invalid_credentials",
      message: "Authentication failed",
    });
  });

  it("preserves account status error codes for actionable login messages", () => {
    expect(
      parseAuthError({
        detail: {
          code: "registration_pending",
          message: "Awaiting approval",
        },
      }),
    ).toEqual({ code: "registration_pending", message: "Awaiting approval" });
    expect(
      parseAuthError({
        detail: { code: "account_disabled", message: "Account disabled" },
      }),
    ).toEqual({ code: "account_disabled", message: "Account disabled" });
  });

  it("validates the registration pending response", () => {
    expect(
      registrationPendingResponseSchema.safeParse({
        status: ACCOUNT_STATUS.PENDING,
        message: "Application submitted",
      }).success,
    ).toBe(true);
    expect(
      registrationPendingResponseSchema.safeParse({
        status: ACCOUNT_STATUS.ACTIVE,
        message: "Application submitted",
      }).success,
    ).toBe(false);
  });
});
