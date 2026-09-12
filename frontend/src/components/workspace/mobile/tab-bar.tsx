"use client";

import { BotIcon, MessageSquareIcon, SettingsIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/**
 * Root screens of the mobile shell (prototype ①⑥⑦). Every other screen is
 * reached from one of these, so the bar carries exactly three entries.
 */
const TABS = [
  { href: "/m/workspace", icon: MessageSquareIcon, key: "chats" },
  { href: "/m/agents", icon: BotIcon, key: "agents" },
  { href: "/m/settings", icon: SettingsIcon, key: "settings" },
] as const;

/**
 * `usePathname()` reports the URL the browser is actually on, not the path the
 * middleware rewrote to: entering `/workspace/chats/abc` renders
 * `/m/workspace/chats/[thread_id]` while `usePathname()` still says
 * `/workspace/chats/abc` (verified against the dev server). Normalizing both
 * shapes makes the bar behave the same whether the user arrived through the
 * rewrite or navigated straight to `/m/...`.
 */
export function normalizeMobilePathname(pathname: string): string {
  if (pathname === "/m" || pathname.startsWith("/m/")) {
    return pathname;
  }
  return `/m${pathname}`;
}

/** A tab stays lit on its descendants, so drilling in does not blank the bar. */
export function isTabActive(pathname: string, href: string): boolean {
  const normalized = normalizeMobilePathname(pathname);
  return normalized === href || normalized.startsWith(`${href}/`);
}

/**
 * Screens that own the whole viewport and hide the tab bar.
 *
 * These are the signed-out routes: login, admin setup, and the SSO callback.
 * None of the three has a meaningful tab to highlight — the user is not in the
 * workspace yet — and on a 390px column the bar would eat ~56px of a form that
 * already has to clear the software keyboard. The shared auth layout takes over
 * the bottom safe-area inset it would otherwise carry.
 */
const TAB_BAR_HIDDEN_PREFIXES = ["/login", "/setup", "/auth/callback"] as const;

export function shouldHideMobileTabBar(pathname: string): boolean {
  // Normalize both sides: `pathname` may arrive either pre- or post-rewrite,
  // while the prefixes are written as the public paths a developer recognises.
  const normalized = normalizeMobilePathname(pathname);
  return TAB_BAR_HIDDEN_PREFIXES.some((prefix) => {
    const mobilePrefix = normalizeMobilePathname(prefix);
    return (
      normalized === mobilePrefix || normalized.startsWith(`${mobilePrefix}/`)
    );
  });
}

export function MobileTabBar({ className }: { className?: string }) {
  const pathname = usePathname();
  const { t } = useI18n();

  if (shouldHideMobileTabBar(pathname)) {
    return null;
  }

  const labels = {
    chats: t.sidebar.chats,
    agents: t.sidebar.agents,
    settings: t.settings.title,
  } satisfies Record<(typeof TABS)[number]["key"], string>;

  return (
    <nav
      className={cn(
        "bg-card flex shrink-0 border-t pb-[env(safe-area-inset-bottom)]",
        className,
      )}
    >
      {TABS.map(({ href, icon: Icon, key }) => {
        const active = isTabActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex min-h-14 flex-1 flex-col items-center justify-center gap-1 pt-2 text-[11px]",
              active
                ? "text-foreground font-semibold"
                : "text-muted-foreground",
            )}
          >
            <Icon className="size-5" />
            {labels[key]}
          </Link>
        );
      })}
    </nav>
  );
}
