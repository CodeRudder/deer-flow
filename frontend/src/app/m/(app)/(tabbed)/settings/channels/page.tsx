"use client";

import { MobileChannelsSection } from "@/components/workspace/mobile/settings/channels-section";
import { MobileSettingsSubpage } from "@/components/workspace/mobile/settings/settings-subpage";
import { useI18n } from "@/core/i18n/hooks";

/**
 * Channels (S6) — degraded: connection status only.
 *
 * Deliberately *not* marked `readOnly`: the prototype badges only the two
 * model/skill rows. The section's own notice says the binding flow stays on the
 * desktop.
 */
export default function MobileChannelsSettingsPage() {
  const { t } = useI18n();

  return (
    <MobileSettingsSubpage title={t.settings.sections.channels}>
      <MobileChannelsSection />
    </MobileSettingsSubpage>
  );
}
