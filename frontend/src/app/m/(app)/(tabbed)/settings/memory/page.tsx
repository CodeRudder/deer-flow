"use client";

import { MobileMemorySection } from "@/components/workspace/mobile/settings/memory-section";
import { MobileSettingsSubpage } from "@/components/workspace/mobile/settings/settings-subpage";
import { useI18n } from "@/core/i18n/hooks";

/** Memory (S4): the user's own memory, readable and writable. */
export default function MobileMemorySettingsPage() {
  const { t } = useI18n();

  return (
    <MobileSettingsSubpage title={t.settings.sections.memory}>
      <MobileMemorySection />
    </MobileSettingsSubpage>
  );
}
