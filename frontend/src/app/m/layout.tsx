import { type Viewport } from "next";

/**
 * `viewport-fit=cover` unlocks the `env(safe-area-inset-*)` values the tab bar
 * and the composers rely on. Client-side zoom stays enabled — iOS auto-zooms
 * into any input below 16px, which is a layout bug, not a feature.
 */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

/**
 * Mobile shell. Deliberately free of auth gating, providers and chrome: the
 * branches under it own all three, and they disagree about them — `(auth)` is
 * bare and full-bleed, `(app)` is guarded and splits again into the screens
 * that keep the tab bar (`(tabbed)`) and the ones that own the whole viewport
 * (`(fullbleed)`). This file supplies only what all of them need, the `100dvh`
 * column that makes `min-h-full` inside a branch mean "the visible height".
 *
 * The scaffolding that used to live here (`<main>` + `MobileTabBar`) moved into
 * the branches for that reason: a bar rendered from the root would also render
 * on the sign-in screens, which is what forced the pathname-prefix hide rule
 * that the route groups now express structurally. See `(app)/layout.tsx`.
 */
export default function MobileLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="bg-background text-foreground flex h-[100dvh] flex-col overflow-hidden">
      {children}
    </div>
  );
}
