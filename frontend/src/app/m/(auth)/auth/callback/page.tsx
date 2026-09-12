"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { resolveSafeRedirect } from "@/lib/safe-redirect";

/**
 * Landing point after an SSO round-trip (prototype frame ⑧'s sibling flow).
 *
 * Same probe-and-redirect as the desktop callback: ask `/api/v1/auth/me` whether
 * the cookie the provider just set is valid, then either continue to `next` or
 * bounce back to sign-in with `?error=sso_failed`. Only the presentation is
 * mobile — a centred spinner instead of the desktop full-page splash.
 */
export default function MobileAuthCallbackPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { t } = useI18n();
  const [status, setStatus] = useState<"loading" | "success" | "error">(
    "loading",
  );
  const calledRef = useRef(false);

  const doAuthCheck = useCallback(async () => {
    // React 19 can run an effect twice in development; the probe must not.
    if (calledRef.current) return;
    calledRef.current = true;

    const next = resolveSafeRedirect(searchParams.get("next"));

    try {
      const res = await fetch("/api/v1/auth/me", { credentials: "include" });

      if (res.ok) {
        setStatus("success");
        // Brief pause so the success state is perceptible rather than a flash.
        setTimeout(() => router.replace(next), 300);
      } else {
        setStatus("error");
        setTimeout(() => router.replace("/login?error=sso_failed"), 1500);
      }
    } catch {
      setStatus("error");
      setTimeout(() => router.replace("/login?error=sso_failed"), 1500);
    }
  }, [searchParams, router]);

  useEffect(() => {
    void doAuthCheck();
  }, [doAuthCheck]);

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
      {status === "loading" && (
        <>
          <div className="size-8 animate-spin rounded-full border-2 border-current border-t-transparent" />
          <p className="text-muted-foreground text-sm">{t.login.signingIn}</p>
        </>
      )}
      {status === "success" && (
        <p className="text-muted-foreground text-sm">{t.login.redirecting}</p>
      )}
      {status === "error" && (
        <p className="text-muted-foreground text-sm">
          {t.login.authFailedRedirecting}
        </p>
      )}
    </div>
  );
}
