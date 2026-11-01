"use client";

import {
  BellIcon,
  BoxesIcon,
  BrainIcon,
  CableIcon,
  InfoIcon,
  PaletteIcon,
  SparklesIcon,
  UserIcon,
} from "lucide-react";
import { useTheme } from "next-themes";

import { APP_VERSION } from "@/components/workspace/mobile/settings/app-version";
import { useMobileSettingsCopy } from "@/components/workspace/mobile/settings/settings-copy";
import {
  MobileSettingsGroup,
  MobileSettingsRow,
} from "@/components/workspace/mobile/settings/settings-list";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";

/**
 * Mobile settings root — the tab-bar entry (prototype ⑦).
 *
 * The screen is a grouped list of drill-downs. Four groups, seven rows:
 *
 * | group              | rows                          |
 * | ------------------ | ----------------------------- |
 * | 个人                | account / appearance / notification / memory |
 * | 连接                | channels                      |
 * | 模型与能力           | models* / skills*             |
 * | 其他                | about                         |
 *
 * `*` marks the two read-only rows: they open lists that can be *read* on the
 * phone while every write stays on the desktop (`FEATURE_LIST.md` §1.4), and
 * they are badged on the row so that is visible before the tap, not after.
 *
 * The MCP tools row is deliberately **absent** (S7: administrator
 * configuration, removed from mobile entirely) — not hidden behind a flag, just
 * not rendered.
 *
 * Every `href` is the public `/settings/...` path. The middleware rewrites a
 * phone onto `/m/settings/...`, so the address bar never shows the internal
 * prefix (plan §1.2.1) — the same rule the tab bar and the thread rows follow.
 *
 * The values on the right are the three that cost nothing: the session's own
 * email (`useAuth()`, already in context), the theme this browser resolved
 * (`next-themes`, already on the client), and the build's version. Nothing is
 * fetched on this screen — no section's data load starts until its own
 * screen opens, which is also why the list cannot show a per-section status
 * ("2 connected"): that would mean loading every backend here.
 */
export default function MobileSettingsPage() {
  const { t } = useI18n();
  const copy = useMobileSettingsCopy();
  const { user } = useAuth();
  const { theme } = useTheme();
  const currentTheme = (theme ?? "system") as "system" | "light" | "dark";

  return (
    <div className="px-4 py-6">
      <h1 className="mb-6 text-xl font-semibold">{t.settings.title}</h1>

      <MobileSettingsGroup title={copy.groups.personal}>
        <MobileSettingsRow
          href="/settings/account"
          icon={UserIcon}
          label={t.settings.sections.account}
          value={user?.email}
          testId="mobile-settings-row-account"
        />
        <MobileSettingsRow
          href="/settings/appearance"
          icon={PaletteIcon}
          label={t.settings.sections.appearance}
          value={t.settings.appearance[currentTheme]}
          testId="mobile-settings-row-appearance"
        />
        <MobileSettingsRow
          href="/settings/notification"
          icon={BellIcon}
          label={t.settings.sections.notification}
          testId="mobile-settings-row-notification"
        />
        <MobileSettingsRow
          href="/settings/memory"
          icon={BrainIcon}
          label={t.settings.sections.memory}
          testId="mobile-settings-row-memory"
        />
      </MobileSettingsGroup>

      <MobileSettingsGroup title={copy.groups.connection}>
        <MobileSettingsRow
          href="/settings/channels"
          icon={CableIcon}
          label={t.settings.sections.channels}
          testId="mobile-settings-row-channels"
        />
      </MobileSettingsGroup>

      <MobileSettingsGroup title={copy.groups.capabilities}>
        <MobileSettingsRow
          href="/settings/models"
          icon={BoxesIcon}
          label={t.settings.sections.models}
          readOnly
          testId="mobile-settings-row-models"
        />
        <MobileSettingsRow
          href="/settings/skills"
          icon={SparklesIcon}
          label={t.settings.sections.skills}
          readOnly
          testId="mobile-settings-row-skills"
        />
      </MobileSettingsGroup>

      <MobileSettingsGroup title={copy.groups.other}>
        <MobileSettingsRow
          href="/settings/about"
          icon={InfoIcon}
          label={t.settings.sections.about}
          value={APP_VERSION}
          testId="mobile-settings-row-about"
        />
      </MobileSettingsGroup>
    </div>
  );
}
