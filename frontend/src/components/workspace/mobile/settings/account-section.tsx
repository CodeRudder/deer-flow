"use client";

import { LogOutIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { MobileAuthField, MobileAuthLabel } from "@/components/workspace/mobile/auth-field";
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { parseAuthError } from "@/core/auth/types";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/**
 * Account section of the mobile settings screen (prototype ⑦, S1).
 *
 * The behaviour is the desktop `AccountSettingsPage`'s, unchanged: the same
 * endpoint, the same two client-side checks before it, the same CSRF headers,
 * the same SSO branch. What differs is only the shape, because a 390px column
 * cannot take the desktop's layout:
 *
 * - the desktop reads the profile out of a `grid-cols-[max-content_max-content]`
 *   table. An email address is the longest string on this screen and the one
 *   value that cannot be wrapped to fit, so the values stack under their labels
 *   instead of sitting beside them.
 * - the fields are `MobileAuthField` (16px text, 48px tall) rather than the
 *   shared `Input`, whose `md:text-sm` would put the page into iOS's
 *   focus-zoom. Same reason as the auth screens — the rule lives in one place.
 * - the two buttons are full-width and clear the 44px floor, so the destructive
 *   one is not a small target next to the password form.
 */
export function MobileAccountSection() {
  const { user, logout } = useAuth();
  const { t } = useI18n();
  const isSsoUser = Boolean(user?.oauth_provider);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setMessage("");

    if (newPassword !== confirmPassword) {
      setError(t.settings.account.passwordMismatch);
      return;
    }
    if (newPassword.length < 8) {
      setError(t.settings.account.passwordTooShort);
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
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      setMessage(t.settings.account.passwordChangedSuccess);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch {
      setError(t.settings.account.networkError);
    } finally {
      setLoading(false);
    }
  };

  /**
   * Label-above-value rows; `break-all` keeps a long address inside the column.
   *
   * `capitalize` is per row, not on the value column: the desktop capitalizes
   * the role and the provider because both are lowercase slugs, but applying it
   * to an email address would upper-case its first letter.
   */
  const profileRows: Array<{ label: string; value: string; capitalize?: boolean }> =
    [
      { label: t.settings.account.email, value: user?.email ?? "—" },
      {
        label: t.settings.account.role,
        value: user?.system_role ?? "—",
        capitalize: true,
      },
    ];
  if (isSsoUser) {
    profileRows.push({
      label: t.settings.account.ssoProvider,
      value: user?.oauth_provider ?? "—",
      capitalize: true,
    });
  }

  return (
    <div className="space-y-8">
      <SettingsSection title={t.settings.account.profileTitle}>
        <dl className="space-y-3">
          {profileRows.map(({ label, value, capitalize }) => (
            <div key={label}>
              <dt className="text-muted-foreground text-[13px]">{label}</dt>
              <dd
                className={cn(
                  "text-base font-medium break-all",
                  capitalize && "capitalize",
                )}
              >
                {value}
              </dd>
            </div>
          ))}
        </dl>
      </SettingsSection>

      {!isSsoUser ? (
        <SettingsSection
          title={t.settings.account.changePasswordTitle}
          description={t.settings.account.changePasswordDescription}
        >
          <form onSubmit={handleChangePassword} className="space-y-4">
            <div>
              <MobileAuthLabel htmlFor="mobile-current-password">
                {t.settings.account.currentPassword}
              </MobileAuthLabel>
              <MobileAuthField
                id="mobile-current-password"
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                required
              />
            </div>
            <div>
              <MobileAuthLabel htmlFor="mobile-new-password">
                {t.settings.account.newPassword}
              </MobileAuthLabel>
              <MobileAuthField
                id="mobile-new-password"
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={8}
              />
            </div>
            <div>
              <MobileAuthLabel htmlFor="mobile-confirm-password">
                {t.settings.account.confirmNewPassword}
              </MobileAuthLabel>
              <MobileAuthField
                id="mobile-confirm-password"
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                minLength={8}
              />
            </div>
            {/* `role` rather than a bare colour: the desktop sets these with
                `text-red-500`/`text-green-500` alone, which a screen reader
                never announces as an outcome. */}
            {error && (
              <p role="alert" className="text-sm text-red-500">
                {error}
              </p>
            )}
            {message && (
              <p role="status" className="text-sm text-green-500">
                {message}
              </p>
            )}
            <Button
              type="submit"
              variant="outline"
              className="min-h-12 w-full text-base"
              disabled={loading}
            >
              {loading
                ? t.settings.account.updating
                : t.settings.account.updatePassword}
            </Button>
          </form>
        </SettingsSection>
      ) : (
        <SettingsSection
          title={t.settings.account.changePasswordTitle}
          description={t.settings.account.ssoPasswordDescription}
        >
          <p className="text-muted-foreground text-base">
            {t.settings.account.ssoPasswordMessage.replace(
              "{provider}",
              user?.oauth_provider ?? "",
            )}
          </p>
        </SettingsSection>
      )}

      <SettingsSection title="" description="">
        <Button
          variant="destructive"
          onClick={logout}
          className="min-h-12 w-full gap-2 text-base"
        >
          <LogOutIcon className="size-5" />
          {t.settings.account.signOut}
        </Button>
      </SettingsSection>
    </div>
  );
}
