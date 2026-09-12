"use client";

import { BotIcon, MessageSquareIcon, SettingsIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/**
 * Root screens of the mobile shell (prototype ①⑥⑦). Every other screen is
 * reached from one of these, so the bar carries exactly three entries.
 *
 * The hrefs are the **public** paths, not the `/m/*` ones. `<Link>` pushes its
 * href straight into the address bar and no middleware runs for a client-side
 * push of an already-`/m/` path (the matcher skips that prefix), so linking to
 * `/m/workspace` would write the internal prefix into the URL the user sees,
 * bookmarks and shares — verified in a real browser. Plan §1.2.1: the address
 * bar must never show `/m/`. Linking to the public path lets the middleware
 * re-land a phone on the mobile tree without the prefix ever surfacing.
 */
const TABS = [
  { href: "/workspace", icon: MessageSquareIcon, key: "chats" },
  { href: "/agents", icon: BotIcon, key: "agents" },
  { href: "/settings", icon: SettingsIcon, key: "settings" },
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

/**
 * A tab stays lit on its descendants, so drilling in does not blank the bar.
 *
 * Both sides are normalized: `pathname` arrives either pre- or post-rewrite,
 * and `href` is the public path by design (see `TABS`).
 */
export function isTabActive(pathname: string, href: string): boolean {
  const normalized = normalizeMobilePathname(pathname);
  const target = normalizeMobilePathname(href);
  return normalized === target || normalized.startsWith(`${target}/`);
}

/**
 * Screens that own the whole viewport and hide the tab bar.
 *
 * These are the signed-out routes: login, admin setup, and the SSO callback.
 * None of the three has a meaningful tab to highlight — the user is not in the
 * workspace yet — and on a 390px column the bar would eat ~56px of a form that
 * already has to clear the software keyboard. The shared auth layout takes over
 * the bottom safe-area inset it would otherwise carry.
 *
 * The chat screen (T5) keeps the bar on purpose — `shouldHideMobileTabBar` is
 * pinned by `tests/unit/.../tab-bar.test.ts` as "the bar stays on workspace
 * screens" — so the composer sits above it rather than replacing it.
 */
const TAB_BAR_HIDDEN_PREFIXES = ["/login", "/setup", "/auth/callback"] as const;

/**
 * The full-screen artifact view (T6), which owns the bottom edge instead.
 *
 * Prototype ⑤ is a full-bleed screen with one action bar at the bottom; a tab
 * bar under it would stack two bars and split the safe-area inset between
 * them. It is matched by shape rather than by prefix because the thread id
 * sits in the middle — a `startsWith("/workspace/chats")` would take the chat
 * screen's bar away too.
 */
const TAB_BAR_HIDDEN_PATTERN =
  /^\/m\/workspace\/chats\/[^/]+\/artifacts(?:\/|$)/;

export function shouldHideMobileTabBar(pathname: string): boolean {
  // Normalize both sides: `pathname` may arrive either pre- or post-rewrite,
  // while the prefixes are written as the public paths a developer recognises.
  const normalized = normalizeMobilePathname(pathname);
  if (TAB_BAR_HIDDEN_PATTERN.test(normalized)) {
    return true;
  }
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
