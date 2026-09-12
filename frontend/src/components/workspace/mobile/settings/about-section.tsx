"use client";

import { APP_VERSION } from "@/components/workspace/mobile/settings/app-version";
import { AboutSettingsPage } from "@/components/workspace/settings/about-settings-page";
import { useI18n } from "@/core/i18n/hooks";

/**
 * About (S9), retained on mobile.
 *
 * The version is the one value the prototype puts on this screen
 * (`v2.1.0`), read from the build's own `package.json` (see `app-version.ts`)
 * and labelled with the shared `t.common.version`. Under it the desktop's
 * `AboutSettingsPage` renders unchanged — it is already a markdown document
 * rendered by `SafeStreamdown`, whose mobile behaviour (long lines wrap rather
 * than overflow) is the one T13 fixed for the chat transcript, so there is
 * nothing to re-shape.
 */
export function MobileAboutSection() {
  const { t } = useI18n();

  return (
    <div className="space-y-6">
      <div className="bg-card flex min-h-14 items-center justify-between gap-3 rounded-xl border p-3">
        <span className="text-base font-medium">{t.common.version}</span>
        <span
          data-testid="mobile-about-version"
          className="text-muted-foreground text-sm"
        >
          {APP_VERSION}
        </span>
      </div>

      <AboutSettingsPage />
    </div>
  );
}
