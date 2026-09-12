import { type ReactNode } from "react";

import { MobileTabBar } from "@/components/workspace/mobile/tab-bar";

/**
 * The signed-in screens that keep the tab bar.
 *
 * Route-group split (T17): `(app)` decides *who may see the screen* (one guard
 * for the whole signed-in tree), and these two groups decide *what the bottom
 * edge is*. A root screen — the thread list, `/agents`, `/settings` — sits here
 * and gets the bar; a screen whose own footer owns the bottom edge (the thread's
 * composer, the artifact viewer's action bar) sits in `(fullbleed)` and does
 * not.
 *
 * That makes "no tab bar here" structure rather than a pathname test: the bar is
 * rendered by this layout only, so adding another full-screen route means
 * putting it in `(fullbleed)` — there is no hide rule to remember to update. It
 * is the same move `(auth)` already makes for the signed-out screens, one level
 * down.
 *
 * The thread screen deliberately sits in the other group: prototype ①⑥⑦ give the
 * bar to the three *roots*, while ② draws the composer straight onto the home
 * indicator. Two bottom bars would also split the safe-area inset between them.
 *
 * The `<main>` is owned here rather than by `(app)`: the bar has to be its
 * *sibling* (both are children of the `100dvh` column in `app/m/layout.tsx`),
 * and a bar rendered below a shared `<main>` would sit inside its scroll
 * container. `(auth)/layout.tsx` supplies the same element for the same reason.
 */
export default function MobileTabbedLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <>
      <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {children}
      </main>
      <MobileTabBar />
    </>
  );
}
