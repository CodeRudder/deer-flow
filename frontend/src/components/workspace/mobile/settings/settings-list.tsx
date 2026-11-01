"use client";

import { ChevronRightIcon, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { useMobileSettingsCopy } from "@/components/workspace/mobile/settings/settings-copy";

/**
 * The two atoms prototype ⑦ is built from: a titled group of rows, and one
 * row with its chevron.
 *
 * The desktop has no equivalent — its settings are a `md:grid-cols-[220px_1fr]`
 * sidebar (`settings-dialog.tsx`) whose entries are selected in place. A phone
 * drills down instead, so each row is a `<Link>` to a full screen, and the
 * public path (`/settings/models`) is what it carries: the middleware decides
 * which tree renders, so `/m/` never reaches the address bar (plan §1.2.1).
 *
 * `min-h-14` (56px) is the touch floor with room to spare; the chevron and the
 * optional value are inside the same link, so the whole row — not a small
 * icon — is the target.
 */
export function MobileSettingsGroup({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="mb-6">
      <h2 className="text-muted-foreground mb-2 px-1 text-[13px] font-medium">
        {title}
      </h2>
      <ul className="bg-card divide-y overflow-hidden rounded-2xl border">
        {children}
      </ul>
    </section>
  );
}

/**
 * One row: icon, label, optional read-only pill, optional value, chevron.
 *
 * The pill is on the *row*, not only inside the screen it opens: the prototype
 * is explicit that a read-only area must not be discovered by tapping into it
 * ("不要做成「点进去才发现不能改」").
 *
 * The value is a plain string, not a node: it is always a short data-derived
 * label (an email address, a theme name, a version) and truncating it needs the
 * `max-w` below to hold. `truncate` keeps a long email from pushing the
 * chevron off a 360px screen.
 */
export function MobileSettingsRow({
  href,
  icon: Icon,
  label,
  value,
  readOnly = false,
  testId,
}: {
  href: string;
  icon: LucideIcon;
  label: string;
  value?: string;
  readOnly?: boolean;
  testId?: string;
}) {
  const copy = useMobileSettingsCopy();

  return (
    <li>
      <Link
        href={href}
        data-testid={testId}
        className="active:bg-accent flex min-h-14 items-center gap-3 px-4 py-3"
      >
        <Icon
          aria-hidden="true"
          className="text-muted-foreground size-5 shrink-0"
        />
        <span className="min-w-0 flex-1 truncate text-base">{label}</span>
        {readOnly ? (
          <Badge variant="secondary" className="shrink-0">
            {copy.readOnlyBadge}
          </Badge>
        ) : null}
        {value ? (
          <span className="text-muted-foreground max-w-[45%] truncate text-sm">
            {value}
          </span>
        ) : null}
        <ChevronRightIcon
          aria-hidden="true"
          className="text-muted-foreground size-5 shrink-0"
        />
      </Link>
    </li>
  );
}
