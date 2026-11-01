"use client";

import { MobileNotificationSection } from "@/components/workspace/mobile/settings/notification-section";
import { MobileSettingsSubpage } from "@/components/workspace/mobile/settings/settings-subpage";
import { useI18n } from "@/core/i18n/hooks";

/** Notification (S3). */
export default function MobileNotificationSettingsPage() {
  const { t } = useI18n();

  return (
    <MobileSettingsSubpage title={t.settings.sections.notification}>
      <MobileNotificationSection />
    </MobileSettingsSubpage>
  );
}
