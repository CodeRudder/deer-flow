"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  MobileAuthField,
  MobileAuthLabel,
} from "@/components/workspace/mobile/auth-field";
import { useAuth } from "@/core/auth/AuthProvider";
import { shouldShowSsoHint } from "@/core/auth/login";
import {
  canCreateRegularAccount,
  fetchSetupStatus,
  type SetupStatusResponse,
} from "@/core/auth/setup";
import {
  parseAuthError,
  registrationPendingResponseSchema,
} from "@/core/auth/types";
import { useI18n } from "@/core/i18n/hooks";
import { resolveSafeRedirect } from "@/lib/safe-redirect";

/**
 * Mobile sign-in / sign-up (prototype ⑧).
 *
 * The request flow is the desktop login page's, screen for screen: same three
 * endpoints, same body shapes (`x-www-form-urlencoded` for login, JSON for
 * register), same 202 pending-approval branch, same SSO-only hint. Only the
 * presentation differs, because a phone keyboard and a 390px column make the
 * desktop layout unusable.
 *
 * Redirects deliberately use the *public* `/workspace` path. `<Link>` and
 * `router.push` issue an RSC request that the middleware sees, so a phone lands
 * on `/m/workspace` while the address bar keeps showing the public URL.
 */
export default function MobileLoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isAuthenticated } = useAuth();
  const { t } = useI18n();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isLogin, setIsLogin] = useState(true);
  const [ssoProviders, setSsoProviders] = useState<
    { id: string; display_name: string; type: string }[]
  >([]);
  const [setupStatus, setSetupStatus] = useState<SetupStatusResponse | null>(
    null,
  );
  const [setupStatusChecked, setSetupStatusChecked] = useState(false);

  // Seed the error from `?error=sso_failed`, the way the SSO callback returns.
  const errorParam = searchParams.get("error");
  const [error, setError] = useState(
    errorParam
      ? (t.login.errors[errorParam as keyof typeof t.login.errors] ??
          t.login.authFailed)
      : "",
  );
  // Soft hint after a failed sign-in when SSO is configured: an SSO-only account
  // has no local password, but the backend answers a generic
  // "incorrect email or password" on purpose (no account enumeration), so the
  // only nudge available is "try the buttons below".
  const [showSsoHint, setShowSsoHint] = useState(false);
  const [registrationPending, setRegistrationPending] = useState(false);
  const [loading, setLoading] = useState(false);

  const redirectPath = resolveSafeRedirect(searchParams.get("next"));
  const regularSignupAllowed = canCreateRegularAccount({
    checked: setupStatusChecked,
    status: setupStatus,
  });
  const systemNeedsAdminSetup = setupStatus?.needs_setup === true;

  // Already signed in (e.g. arrived here after a cookie was set elsewhere).
  useEffect(() => {
    if (isAuthenticated) {
      router.push(redirectPath);
    }
  }, [isAuthenticated, redirectPath, router]);

  // Setup state decides whether "sign up" is offered at all; the providers call
  // decides whether the SSO block renders. Both are best-effort — a failure
  // just hides the corresponding affordance.
  useEffect(() => {
    let cancelled = false;

    void fetchSetupStatus()
      .then((data) => {
        if (cancelled) return;
        setSetupStatus(data);
        if (data.needs_setup) {
          setIsLogin(true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSetupStatus(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setSetupStatusChecked(true);
        }
      });

    void fetch("/api/v1/auth/providers")
      .then((r) => r.json())
      .then(
        (data: {
          providers: { id: string; display_name: string; type: string }[];
        }) => {
          if (!cancelled) {
            setSsoProviders(data.providers ?? []);
          }
        },
      )
      .catch(() => {
        // Ignore errors; no SSO providers shown
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setShowSsoHint(false);
    setLoading(true);

    if (!isLogin && !regularSignupAllowed) {
      setError(t.login.adminSetupRequiredDescription);
      setLoading(false);
      return;
    }

    try {
      const endpoint = isLogin
        ? "/api/v1/auth/login/local"
        : "/api/v1/auth/register";
      const body = isLogin
        ? `username=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`
        : JSON.stringify({ email, password });

      const headers: HeadersInit = isLogin
        ? { "Content-Type": "application/x-www-form-urlencoded" }
        : { "Content-Type": "application/json" };

      const res = await fetch(endpoint, {
        method: "POST",
        headers,
        body,
        credentials: "include", // Important: include HttpOnly cookie
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        const localizedMessage =
          authError.code === "registration_pending" ||
          authError.code === "account_disabled"
            ? t.login.errors[authError.code]
            : authError.message;
        setError(localizedMessage);
        // A wrong password may mean an SSO-only account; other failures, such as
        // a blocked email domain, keep their own actionable message.
        setShowSsoHint(
          shouldShowSsoHint({
            isLogin,
            errorCode: authError.code,
            providerCount: ssoProviders.length,
          }),
        );
        return;
      }

      // Registration can come back 202: accepted, but awaiting an administrator.
      if (!isLogin && res.status === 202) {
        const pending = registrationPendingResponseSchema.safeParse(
          await res.json(),
        );
        if (!pending.success) {
          setError(t.login.authFailed);
          return;
        }
        setPassword("");
        setRegistrationPending(true);
        return;
      }

      // Both login and register set a cookie — redirect to the workspace.
      router.push(redirectPath);
    } catch {
      setError(t.login.networkError);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="w-full">
      <div className="mb-8 text-center">
        <h1 className="text-2xl font-semibold">DeerFlow</h1>
        <p className="text-muted-foreground mt-1.5 text-sm">
          {isLogin ? t.login.signInTitle : t.login.createAccountTitle}
        </p>
      </div>

      {systemNeedsAdminSetup && (
        <div className="border-l-2 border-blue-500 ps-3 text-sm">
          <p className="font-medium">{t.login.adminSetupRequiredTitle}</p>
          <p className="text-muted-foreground mt-1">
            {t.login.adminSetupRequiredDescription}
          </p>
          <Link
            href="/setup"
            className="mt-2 inline-block font-medium text-blue-500 hover:underline"
          >
            {t.login.createAdminAccount}
          </Link>
        </div>
      )}

      {registrationPending ? (
        <div className="border-border bg-card space-y-4 rounded-2xl border p-5 text-center shadow-sm">
          <div className="mx-auto grid size-10 place-items-center rounded-full bg-emerald-100 text-xl text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
            ✓
          </div>
          <div>
            <h2 className="font-medium">{t.login.registrationPendingTitle}</h2>
            <p className="text-muted-foreground mt-2 text-sm">
              {t.login.registrationPendingDescription}
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            className="h-12 w-full rounded-xl"
            onClick={() => {
              setRegistrationPending(false);
              setIsLogin(true);
              setError("");
            }}
          >
            {t.login.registrationPendingBackToLogin}
          </Button>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <MobileAuthLabel htmlFor="email">{t.login.email}</MobileAuthLabel>
            <MobileAuthField
              id="email"
              type="email"
              autoComplete="email"
              inputMode="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={t.login.emailPlaceholder}
              required
            />
          </div>
          <div>
            <MobileAuthLabel htmlFor="password">
              {t.login.password}
            </MobileAuthLabel>
            <MobileAuthField
              id="password"
              type="password"
              autoComplete={isLogin ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t.login.passwordPlaceholder}
              required
              minLength={isLogin ? 6 : 8}
            />
          </div>

          {error && <p className="text-sm text-red-500">{error}</p>}

          <Button
            type="submit"
            className="h-12 w-full rounded-xl text-base"
            disabled={loading}
          >
            {loading
              ? t.login.pleaseWait
              : isLogin
                ? t.login.signIn
                : t.login.createAccount}
          </Button>
        </form>
      )}

      {!registrationPending && ssoProviders.length > 0 && (
        <div className="space-y-3">
          {isLogin && (
            <div className="relative my-5">
              <div className="absolute inset-0 flex items-center">
                <span className="w-full border-t" />
              </div>
              <div className="relative flex justify-center text-xs uppercase">
                <span className="bg-background text-muted-foreground px-2">
                  {t.login.orContinueWith}
                </span>
              </div>
            </div>
          )}
          {showSsoHint && (
            <p className="text-muted-foreground text-center text-sm">
              {t.login.ssoHint}
            </p>
          )}
          {ssoProviders.map((provider) => (
            <Button
              key={provider.id}
              type="button"
              variant="outline"
              className="h-12 w-full rounded-xl"
              disabled={loading}
              onClick={() => {
                window.location.href = `/api/v1/auth/oauth/${provider.id}?next=${encodeURIComponent(redirectPath)}`;
              }}
            >
              {t.login.continueWith(provider.display_name)}
            </Button>
          ))}
        </div>
      )}

      {!registrationPending && regularSignupAllowed && (
        <div className="mt-6 text-center text-sm">
          <button
            type="button"
            onClick={() => {
              setIsLogin(!isLogin);
              setError("");
              setShowSsoHint(false);
            }}
            className="text-primary inline-flex min-h-11 items-center justify-center px-2 hover:underline"
          >
            {isLogin ? t.login.noAccountSignUp : t.login.haveAccountSignIn}
          </button>
        </div>
      )}

      <div className="text-muted-foreground mt-6 text-center text-xs">
        <a
          href="https://deerflow.tech/"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex min-h-11 items-center justify-center px-2 hover:underline"
        >
          {t.login.officialWebsite}
        </a>
      </div>
    </div>
  );
}
