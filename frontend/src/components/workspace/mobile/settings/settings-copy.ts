"use client";

import type { Locale } from "@/core/i18n";
import { useI18n } from "@/core/i18n/hooks";

/**
 * The strings prototype ⑦ needs that `t.settings.*` does not carry yet.
 *
 * The desktop settings dialog is a flat nav of nine entries
 * (`settings-dialog.tsx`): it never groups them, never marks one read-only, so
 * there are no keys for the four group headings or for the 只读 badge — the
 * only "read only" string in the locale files belongs to the clarification
 * cards (`t.humanInput.readOnly`), a different surface with a different
 * meaning.
 *
 * They live here rather than in `locales/**` because that is outside this
 * change's file scope; keeping them in one typed map (rather than as inline
 * English strings) means both locales stay complete and a missing group cannot
 * render as `undefined`.
 *
 * TODO(i18n): fold these into `t.settings` as `groups.personal` /
 * `groups.connection` / `groups.capabilities` / `groups.other` /
 * `readOnlyBadge` / `readOnlyNotice` and delete this file.
 */
export type MobileSettingsGroupId =
  | "personal"
  | "connection"
  | "capabilities"
  | "other";

interface MobileSettingsCopy {
  groups: Record<MobileSettingsGroupId, string>;
  /** The pill on the 模型 / 技能 rows (prototype ⑦'s `.ro-badge`). */
  readOnlyBadge: string;
  /** The one-line explanation at the top of a read-only section. */
  readOnlyNotice: string;
}

const COPY: Record<Locale, MobileSettingsCopy> = {
  "en-US": {
    groups: {
      personal: "Personal",
      connection: "Connections",
      capabilities: "Models & capabilities",
      other: "Other",
    },
    readOnlyBadge: "Read only",
    readOnlyNotice:
      "Read only: view the list here, add, edit and remove on the desktop.",
  },
  "zh-CN": {
    groups: {
      personal: "个人",
      connection: "连接",
      capabilities: "模型与能力",
      other: "其他",
    },
    readOnlyBadge: "只读",
    readOnlyNotice: "此处只读：可查看列表，增删改请在桌面端完成。",
  },
};

export function useMobileSettingsCopy(): MobileSettingsCopy {
  const { locale } = useI18n();
  return COPY[locale];
}
