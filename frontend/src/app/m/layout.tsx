import { type Viewport } from "next";

import { MobileTabBar } from "@/components/workspace/mobile/tab-bar";

/**
 * `viewport-fit=cover` unlocks the `env(safe-area-inset-*)` values the tab bar
 * and the later composers rely on. Client-side zoom stays enabled — iOS
 * auto-zooms into any input below 16px, which is a layout bug, not a feature.
 */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

/**
 * Mobile shell. Deliberately free of auth gating and providers: the pages
 * under `/m/*` land in later tasks and will bring their own, mirroring how
 * `app/workspace` owns its layout rather than the root layout.
 */
export default function MobileLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="bg-background text-foreground flex h-[100dvh] flex-col overflow-hidden">
      <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {children}
      </main>
      <MobileTabBar />
    </div>
  );
}
