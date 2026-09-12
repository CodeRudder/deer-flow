"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  MobileAuthField,
  MobileAuthLabel,
} from "@/components/workspace/mobile/auth-field";
import { getCsrfHeaders } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import {
  fetchSetupStatus,
  isSystemAlreadyInitializedError,
} from "@/core/auth/setup";
import { parseAuthError } from "@/core/auth/types";
import { useI18n } from "@/core/i18n/hooks";

type SetupMode = "loading" | "init_admin" | "change_password";

/**
 * Mobile admin bootstrap / forced password change.
 *
 * Both endpoints, their bodies, and the CSRF header on the change-password call
 * are the desktop setup page's. Two mobile-only deviations, both noted at the
 * call sites below: the validation messages go through i18n instead of being
 * hardcoded English, and redirects use the public `/workspace` path so the
 * middleware can land a phone on `/m/workspace` without `/m/` reaching the
 * address bar.
 */
export default function MobileSetupPage() {
  const router = useRouter();
  const { user, isAuthenticated } = useAuth();
  const { t } = useI18n();
  const [mode, setMode] = useState<SetupMode>("loading");

  // --- Shared state ---
  const [email, setEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // --- Change-password mode only ---
  const [currentPassword, setCurrentPassword] = useState("");

  useEffect(() => {
    let cancelled = false;

    if (isAuthenticated && user?.needs_setup) {
      setMode("change_password");
    } else if (!isAuthenticated) {
      // No session — only allow the screen if the system has no admin yet.
      void fetchSetupStatus()
        .then((data: { needs_setup?: boolean }) => {
          if (cancelled) return;
          if (data.needs_setup) {
            setMode("init_admin");
          } else {
            router.replace("/login");
          }
        })
        .catch(() => {
          if (!cancelled) router.replace("/login");
        });
    } else {
      // Authenticated and already set up.
      router.replace("/workspace");
    }

    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, user, router]);

  // ── Init-admin handler ─────────────────────────────────────────────
  const handleInitAdmin = async (e: React.SubmitEvent) => {
    e.preventDefault();
    setError("");

    if (newPassword !== confirmPassword) {
      setError(t.login.passwordsDoNotMatch);
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/initialize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email,
          password: newPassword,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        if (isSystemAlreadyInitializedError(data)) {
          router.replace("/login");
          return;
        }
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      router.push("/workspace");
    } catch {
      setError(t.login.networkError);
    } finally {
      setLoading(false);
    }
  };

  // ── Change-password handler ────────────────────────────────────────
  const handleChangePassword = async (e: React.SubmitEvent) => {
    e.preventDefault();
    setError("");

    if (newPassword !== confirmPassword) {
      setError(t.login.passwordsDoNotMatch);
      return;
    }
    if (newPassword.length < 8) {
      setError(t.login.passwordTooShort);
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/change-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getCsrfHeaders(),
        },
        credentials: "include",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
          new_email: email || undefined,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      router.push("/workspace");
    } catch {
      setError(t.login.networkError);
    } finally {
      setLoading(false);
    }
  };

  if (mode === "loading") {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-muted-foreground text-sm">{t.common.loading}</p>
      </div>
    );
  }

  // ── Admin initialization form ──────────────────────────────────────
  if (mode === "init_admin") {
    return (
      <div className="w-full">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold">DeerFlow</h1>
          <p className="text-muted-foreground mt-1.5 text-sm">
            {t.login.setup.createAdminTitle}
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            {t.login.setup.createAdminDescription}
          </p>
        </div>
        <form onSubmit={handleInitAdmin} className="space-y-3">
          <div>
            <MobileAuthLabel htmlFor="email">{t.login.email}</MobileAuthLabel>
            <MobileAuthField
              id="email"
              type="email"
              autoComplete="email"
              inputMode="email"
              placeholder={t.login.emailPlaceholder}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
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
              autoComplete="new-password"
              placeholder={t.login.setup.passwordPlaceholder}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              minLength={8}
            />
          </div>
          <div>
            <MobileAuthLabel htmlFor="confirmPassword">
              {t.login.setup.confirmPassword}
            </MobileAuthLabel>
            <MobileAuthField
              id="confirmPassword"
              type="password"
              autoComplete="new-password"
              placeholder={t.login.setup.confirmPasswordPlaceholder}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              minLength={8}
            />
          </div>
          {error && <p className="text-sm text-red-500">{error}</p>}
          <Button
            type="submit"
            className="h-12 w-full rounded-xl text-base"
            disabled={loading}
          >
            {loading
              ? t.login.setup.creatingAccount
              : t.login.createAdminAccount}
          </Button>
        </form>
      </div>
    );
  }

  // ── Change-password form (needs_setup after login) ─────────────────
  return (
    <div className="w-full">
      <div className="mb-8 text-center">
        <h1 className="text-2xl font-semibold">DeerFlow</h1>
        <p className="text-muted-foreground mt-1.5 text-sm">
          {t.login.setup.completeSetupTitle}
        </p>
        <p className="text-muted-foreground mt-1 text-xs">
          {t.login.setup.completeSetupDescription}
        </p>
      </div>
      <form onSubmit={handleChangePassword} className="space-y-3">
        <MobileAuthField
          type="email"
          autoComplete="email"
          inputMode="email"
          placeholder={t.login.setup.emailPlaceholder}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <MobileAuthField
          type="password"
          autoComplete="current-password"
          placeholder={t.login.setup.currentPassword}
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          required
        />
        <MobileAuthField
          type="password"
          autoComplete="new-password"
          placeholder={t.login.setup.newPassword}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          required
          minLength={8}
        />
        <MobileAuthField
          type="password"
          autoComplete="new-password"
          placeholder={t.login.setup.confirmNewPassword}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          required
          minLength={8}
        />
        {error && <p className="text-sm text-red-500">{error}</p>}
        <Button
          type="submit"
          className="h-12 w-full rounded-xl text-base"
          disabled={loading}
        >
          {loading
            ? t.login.setup.settingUp
            : t.login.setup.completeSetupAction}
        </Button>
      </form>
    </div>
  );
}
