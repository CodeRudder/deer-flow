import { describe, expect, it } from "@rstest/core";

import { shouldShowSsoHint } from "@/core/auth/login";

describe("shouldShowSsoHint", () => {
  it("shows the hint only for a failed login with invalid credentials and an SSO provider", () => {
    expect(
      shouldShowSsoHint({
        isLogin: true,
        errorCode: "invalid_credentials",
        providerCount: 1,
      }),
    ).toBe(true);

    expect(
      shouldShowSsoHint({
        isLogin: false,
        errorCode: "invalid_credentials",
        providerCount: 1,
      }),
    ).toBe(false);

    expect(
      shouldShowSsoHint({
        isLogin: true,
        errorCode: "email_domain_not_allowed",
        providerCount: 1,
      }),
    ).toBe(false);

    expect(
      shouldShowSsoHint({
        isLogin: true,
        errorCode: "invalid_credentials",
        providerCount: 0,
      }),
    ).toBe(false);
  });
});
