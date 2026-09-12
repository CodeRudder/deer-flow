"use client";

import { MobileModelsSection } from "@/components/workspace/mobile/settings/models-section";
import { MobileSettingsSubpage } from "@/components/workspace/mobile/settings/settings-subpage";
import { useI18n } from "@/core/i18n/hooks";

/**
 * Models (S5) — read-only. The pill in the header is the badge the row
 * carries, so the screen repeats the constraint rather than leaving it at the
 * door.
 */
export default function MobileModelsSettingsPage() {
  const { t } = useI18n();

  return (
    <MobileSettingsSubpage title={t.settings.sections.models} readOnly>
      <MobileModelsSection />
    </MobileSettingsSubpage>
  );
}
