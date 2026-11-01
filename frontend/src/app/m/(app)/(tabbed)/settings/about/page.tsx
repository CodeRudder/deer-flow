"use client";

import { MobileAboutSection } from "@/components/workspace/mobile/settings/about-section";
import { MobileSettingsSubpage } from "@/components/workspace/mobile/settings/settings-subpage";
import { useI18n } from "@/core/i18n/hooks";

/** About (S9): the build's version and the shared about document. */
export default function MobileAboutSettingsPage() {
  const { t } = useI18n();

  return (
    <MobileSettingsSubpage title={t.settings.sections.about}>
      <MobileAboutSection />
    </MobileSettingsSubpage>
  );
}
