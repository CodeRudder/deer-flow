"use client";

import { MobileAccountSection } from "@/components/workspace/mobile/settings/account-section";
import { useI18n } from "@/core/i18n/hooks";

/**
 * Mobile settings root (tab-bar entry, prototype ⑦).
 *
 * Only the account section (S1) is built; the remaining sections are listed in
 * `FEATURE_LIST.md` §1.4 and are not placeholders that can be shipped as-is —
 * they are work still owed. They are deliberately *absent* rather than rendered
 * as empty rows, so the screen does not advertise controls that do nothing.
 *
 * Client rather than server: `MobileAccountSection` reads the session through
 * `useAuth()`. The guard is in the parent layout, so there is a session by the
 * time this renders.
 */
export default function MobileSettingsPage() {
  const { t } = useI18n();

  return (
    <div className="px-4 py-6">
      <h1 className="mb-6 text-xl font-semibold">{t.settings.title}</h1>
      <MobileAccountSection />
    </div>
  );
}
