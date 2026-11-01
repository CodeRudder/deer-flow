"use client";

import { MobileAccountSection } from "@/components/workspace/mobile/settings/account-section";
import { MobileSettingsSubpage } from "@/components/workspace/mobile/settings/settings-subpage";
import { useI18n } from "@/core/i18n/hooks";

/**
 * Account (S1) — the section the root screen used to render inline.
 *
 * The content is `MobileAccountSection`, unchanged (profile rows, password
 * change, SSO branch, sign-out); the shell is what is new, so the root can be a
 * list. Client component for the same reason the section is: it reads the
 * session through `useAuth()`.
 */
export default function MobileAccountSettingsPage() {
  const { t } = useI18n();

  return (
    <MobileSettingsSubpage title={t.settings.sections.account}>
      <MobileAccountSection />
    </MobileSettingsSubpage>
  );
}
