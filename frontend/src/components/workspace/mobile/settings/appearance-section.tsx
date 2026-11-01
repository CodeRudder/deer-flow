"use client";

import { CheckIcon } from "lucide-react";
import { useTheme } from "next-themes";
import { useMemo } from "react";

import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { enUS, zhCN, type Locale } from "@/core/i18n";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/**
 * Appearance (S2): theme and language, retained on mobile.
 *
 * The state and the calls are the desktop `AppearanceSettingsPage`'s — the same
 * `next-themes` `setTheme` (which writes the same localStorage key the desktop
 * reads) and the same `changeLocale` from `useI18n` (same cookie) — so a theme
 * or language picked here is the one the desktop tree renders. What differs is
 * the control: the desktop draws three miniature preview cards
 * (`ThemePreviewCard`, ~200px tall, `lg:grid-cols-3`); at 390px they would be
 * a stack of postage stamps, so each option becomes one row of a radio group
 * with its description inline.
 *
 * Native `<input type="radio">` rather than a Radix control: a phone gets the
 * platform's own semantics and keyboard behaviour for free, the row is the
 * target (`min-h-14`), and the group is announced as a group because it is one.
 */
const LANGUAGE_OPTIONS: { value: Locale; label: string }[] = [
  { value: "en-US", label: enUS.locale.localName },
  { value: "zh-CN", label: zhCN.locale.localName },
];

export function MobileAppearanceSection() {
  const { t, locale, changeLocale } = useI18n();
  const { theme, setTheme } = useTheme();
  const currentTheme = (theme ?? "system") as "system" | "light" | "dark";

  const themeOptions = useMemo(
    () => [
      {
        id: "system" as const,
        label: t.settings.appearance.system,
        description: t.settings.appearance.systemDescription,
      },
      {
        id: "light" as const,
        label: t.settings.appearance.light,
        description: t.settings.appearance.lightDescription,
      },
      {
        id: "dark" as const,
        label: t.settings.appearance.dark,
        description: t.settings.appearance.darkDescription,
      },
    ],
    [
      t.settings.appearance.dark,
      t.settings.appearance.darkDescription,
      t.settings.appearance.light,
      t.settings.appearance.lightDescription,
      t.settings.appearance.system,
      t.settings.appearance.systemDescription,
    ],
  );

  return (
    <div className="space-y-8">
      <SettingsSection
        title={t.settings.appearance.themeTitle}
        description={t.settings.appearance.themeDescription}
      >
        <div
          role="radiogroup"
          aria-label={t.settings.appearance.themeTitle}
          className="space-y-2"
        >
          {themeOptions.map((option) => (
            <MobileChoiceRow
              key={option.id}
              name="mobile-theme"
              value={option.id}
              label={option.label}
              description={option.description}
              selected={currentTheme === option.id}
              onSelect={() => setTheme(option.id)}
              testId={`mobile-theme-${option.id}`}
            />
          ))}
        </div>
      </SettingsSection>

      <SettingsSection
        title={t.settings.appearance.languageTitle}
        description={t.settings.appearance.languageDescription}
      >
        <div
          role="radiogroup"
          aria-label={t.settings.appearance.languageTitle}
          className="space-y-2"
        >
          {LANGUAGE_OPTIONS.map((option) => (
            <MobileChoiceRow
              key={option.value}
              name="mobile-language"
              value={option.value}
              label={option.label}
              selected={locale === option.value}
              onSelect={() => changeLocale(option.value)}
              testId={`mobile-language-${option.value}`}
            />
          ))}
        </div>
      </SettingsSection>
    </div>
  );
}

function MobileChoiceRow({
  name,
  value,
  label,
  description,
  selected,
  onSelect,
  testId,
}: {
  name: string;
  value: string;
  label: string;
  description?: string;
  selected: boolean;
  onSelect: () => void;
  testId: string;
}) {
  return (
    <label
      data-testid={testId}
      className={cn(
        "flex min-h-14 w-full cursor-pointer items-center gap-3 rounded-xl border p-3 transition-colors",
        selected ? "border-primary ring-primary/30 ring-2" : "border-input",
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={selected}
        onChange={onSelect}
        className="sr-only"
      />
      <span className="min-w-0 flex-1">
        <span className="block text-base font-medium">{label}</span>
        {description ? (
          <span className="text-muted-foreground block text-[13px] leading-snug">
            {description}
          </span>
        ) : null}
      </span>
      {selected ? (
        <CheckIcon
          aria-hidden="true"
          className="text-primary size-5 shrink-0"
        />
      ) : null}
    </label>
  );
}
