"use client";

import { MobileAppearanceSection } from "@/components/workspace/mobile/settings/appearance-section";
import { MobileSettingsSubpage } from "@/components/workspace/mobile/settings/settings-subpage";
import { useI18n } from "@/core/i18n/hooks";

/** Appearance (S2): theme + language. */
export default function MobileAppearanceSettingsPage() {
  const { t } = useI18n();

  return (
    <MobileSettingsSubpage title={t.settings.sections.appearance}>
      <MobileAppearanceSection />
    </MobileSettingsSubpage>
  );
}
