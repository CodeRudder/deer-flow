import { type AuthErrorCode } from "@/core/auth/types";

interface SsoHintContext {
  isLogin: boolean;
  errorCode: AuthErrorCode;
  providerCount: number;
}

export function shouldShowSsoHint({
  isLogin,
  errorCode,
  providerCount,
}: SsoHintContext): boolean {
  return isLogin && errorCode === "invalid_credentials" && providerCount > 0;
}
