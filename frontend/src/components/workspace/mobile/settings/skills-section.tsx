"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { MobileReadOnlyNotice } from "@/components/workspace/mobile/settings/settings-subpage";
import { useI18n } from "@/core/i18n/hooks";
import { useSkills } from "@/core/skills/hooks";

/**
 * Skills (S8) — read-only on mobile (`FEATURE_LIST.md` §1.4).
 *
 * The list is the desktop `SkillSettingsPage`'s `useSkills()` (same query key,
 * so the caches are one), and so is the public/custom split with `public`
 * selected by default. Three things the desktop has are deliberately absent:
 * the enable `Switch` is rendered **disabled** rather than wired to
 * `useEnableSkill()`, creating a skill is not offered, and the empty state has
 * no action button — all three write, and writes stay on the desktop.
 *
 * A disabled switch rather than a coloured dot: it is the same control the
 * desktop shows, so "enabled" and "disabled" are read the same way on both
 * trees, and its disabled state is itself the message.
 */
export function MobileSkillsSection() {
  const { t } = useI18n();
  const { skills, isLoading, error } = useSkills();
  const [category, setCategory] = useState<string>("public");
  const filteredSkills = useMemo(
    () => skills.filter((skill) => skill.category === category),
    [skills, category],
  );

  return (
    <div className="space-y-4">
      <MobileReadOnlyNotice />

      <div className="flex gap-2">
        {(
          [
            { id: "public", label: t.common.public },
            { id: "custom", label: t.common.custom },
          ] as const
        ).map((option) => (
          <Button
            key={option.id}
            type="button"
            size="sm"
            variant={category === option.id ? "default" : "outline"}
            aria-pressed={category === option.id}
            data-testid={`mobile-skills-filter-${option.id}`}
            className="min-h-11 flex-1 text-base"
            onClick={() => setCategory(option.id)}
          >
            {option.label}
          </Button>
        ))}
      </div>

      {isLoading ? (
        <p className="text-muted-foreground text-base">{t.common.loading}</p>
      ) : error ? (
        <p className="text-destructive text-sm">{error.message}</p>
      ) : filteredSkills.length === 0 ? (
        <p className="text-muted-foreground text-base">
          {t.settings.skills.emptyTitle}
        </p>
      ) : (
        <ul className="space-y-2">
          {filteredSkills.map((skill) => (
            <li
              key={skill.name}
              data-testid="mobile-skill-row"
              className="bg-card flex items-start gap-3 rounded-xl border p-3"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-base font-medium">{skill.name}</p>
                {skill.description ? (
                  <p className="text-muted-foreground line-clamp-4 text-[13px]">
                    {skill.description}
                  </p>
                ) : null}
              </div>
              <Switch
                aria-label={skill.name}
                checked={skill.enabled}
                disabled
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
