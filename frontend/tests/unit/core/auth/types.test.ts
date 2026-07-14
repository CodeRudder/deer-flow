import { describe, expect, it } from "@rstest/core";

import { parseAuthError } from "@/core/auth/types";

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
});
