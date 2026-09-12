import { type ReactNode } from "react";

/**
 * The signed-in screens that own the whole viewport (T17).
 *
 * That is the thread screen (prototype ②), whose composer is its own footer, and
 * the full-screen artifact viewer (prototype ⑤), which paints a bottom action
 * bar. Both carry the bottom safe-area inset themselves; a tab bar underneath
 * would stack two bars and split the inset between them — and because the bar is
 * rendered by the sibling `(tabbed)` layout, this group gets that by *not*
 * rendering it: there is no pathname pattern to keep in sync as full-screen
 * routes are added.
 *
 * The `<main>` mirrors `(tabbed)/layout.tsx` so the screens nested here inherit
 * the box model they had when both groups were one: a definite-height flex item
 * that the artifact surface's `h-full` resolves against.
 */
export default function MobileFullBleedLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
      {children}
    </main>
  );
}
