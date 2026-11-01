"use client";

import { ArrowLeftIcon } from "lucide-react";
import Link from "next/link";
import { type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { useMobileSettingsCopy } from "@/components/workspace/mobile/settings/settings-copy";
import { useI18n } from "@/core/i18n/hooks";

/**
 * The shell every settings sub-screen sits in (prototype ⑦'s drill-down).
 *
 * The screens live under `(tabbed)`, not `(fullbleed)`, so the tab bar stays
 * below them: they have no footer of their own to fight the bar for the bottom
 * edge (the same test `(tabbed)/layout.tsx` documents). The back link is
 * therefore the *only* way up, which is why it is a full 44px target in the
 * header rather than a gesture.
 *
 * `href="/settings"` is the public path, like every other link on the mobile
 * tree — the middleware re-lands a phone on `/m/settings`, and `/m/` never
 * reaches the address bar.
 *
 * The `readOnly` pill mirrors the row's: a screen that cannot be edited says so
 * before the list is read, not after a failed attempt.
 */
export function MobileSettingsSubpage({
  title,
  readOnly = false,
  children,
}: {
  title: string;
  readOnly?: boolean;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const copy = useMobileSettingsCopy();

  return (
    <div className="flex min-h-full flex-col">
      <header className="bg-background/95 sticky top-0 z-10 flex items-center gap-1 border-b px-1 py-1 supports-backdrop-filter:backdrop-blur">
        <Link
          href="/settings"
          aria-label={t.settings.title}
          data-testid="mobile-settings-back"
          className="active:bg-accent flex size-11 shrink-0 items-center justify-center rounded-full"
        >
          <ArrowLeftIcon aria-hidden="true" className="size-5" />
        </Link>
        <h1 className="min-w-0 flex-1 truncate px-1 text-base font-medium">
          {title}
        </h1>
        {readOnly ? (
          <Badge variant="secondary" className="mr-2 shrink-0">
            {copy.readOnlyBadge}
          </Badge>
        ) : null}
      </header>

      <div className="px-4 py-4">{children}</div>
    </div>
  );
}

/**
 * The one-line "edit this on the desktop" notice a read-only section starts
 * with — S5/S8 (read-only by spec) and S6 (binding stays on the desktop).
 */
export function MobileReadOnlyNotice() {
  const copy = useMobileSettingsCopy();

  return (
    <p className="text-muted-foreground bg-muted/50 mb-4 rounded-xl p-3 text-sm">
      {copy.readOnlyNotice}
    </p>
  );
}
