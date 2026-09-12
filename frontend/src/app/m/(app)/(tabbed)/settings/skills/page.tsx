"use client";

import { MobileSettingsSubpage } from "@/components/workspace/mobile/settings/settings-subpage";
import { MobileSkillsSection } from "@/components/workspace/mobile/settings/skills-section";
import { useI18n } from "@/core/i18n/hooks";

/** Skills (S8) — read-only: list and enabled state, no install or toggle. */
export default function MobileSkillsSettingsPage() {
  const { t } = useI18n();

  return (
    <MobileSettingsSubpage title={t.settings.sections.skills} readOnly>
      <MobileSkillsSection />
    </MobileSettingsSubpage>
  );
}
