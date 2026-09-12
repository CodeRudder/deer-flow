import { redirect } from "next/navigation";
import { Toaster } from "sonner";

import { QueryClientProvider } from "@/components/query-client-provider";
import { GatewayOfflineFallback } from "@/components/workspace/gateway-offline-fallback";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { getServerSideUser } from "@/core/auth/server";
import { assertNever } from "@/core/auth/types";

export const dynamic = "force-dynamic";

/**
 * The guard and the providers for every **signed-in** mobile screen.
 *
 * The split is by session, not by path: `(app)` holds everything a signed-in
 * user may reach — the thread list, the chat, the artifact viewer, and the
 * agents/settings roots the tab bar links to — while `(auth)` holds the
 * sign-in flow. Route groups do not appear in the URL, so the public paths are
 * unchanged.
 *
 * T1 originally put this guard on `app/m/workspace/layout.tsx` and rendered the
 * tab bar from `app/m/layout.tsx`. That combination left `/agents` and
 * `/settings` — tab-bar roots — outside the guard, so they would have rendered
 * for a signed-out visitor; and it expressed "hide the bar on auth screens" as
 * a pathname prefix test rather than as structure. Both are fixed here: the
 * guard covers the whole signed-in tree, and `(auth)` simply never renders the
 * bar.
 *
 * It renders no chrome of its own. The bottom edge belongs to the two groups
 * below it (T17): `(tabbed)` renders the scrolling `<main>` *and* the tab bar,
 * `(fullbleed)` renders the `<main>` alone. Both are fragments around
 * `children`, so the DOM is the `100dvh` column from `app/m/layout.tsx` with
 * whichever pair of elements the group asked for — which is why the bar can be
 * a sibling of `<main>` rather than something nested inside its scroll
 * container.
 *
 * Mirrors `app/workspace/layout.tsx` branch for branch — the two trees serve
 * the same data and must not disagree about who may see it. Providers belong
 * next to the guard because `useInfiniteThreads()` needs a `QueryClient`, and
 * that only makes sense once there is a session.
 *
 * Redirect targets are the *public* paths (`/login`, `/setup`, `/workspace`):
 * the middleware re-lands the next request on the mobile tree, which is what
 * keeps `/m/` out of the address bar.
 */
export default async function MobileAppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const result = await getServerSideUser();

  switch (result.tag) {
    case "authenticated":
      return (
        <QueryClientProvider>
          <AuthProvider initialUser={result.user}>{children}</AuthProvider>
          <Toaster position="top-center" />
        </QueryClientProvider>
      );
    case "needs_setup":
    case "system_setup_required":
      redirect("/setup");
    case "unauthenticated":
      redirect("/login");
    case "gateway_unavailable":
      // `GatewayOfflineFallback` supplies its own AuthProvider and probe loop,
      // but not a QueryClient; the list still mounts its hooks underneath, so
      // the provider is repeated here. Unlike the desktop tree there is no
      // shared content wrapper to host the banner, hence `renderBanner`.
      return (
        <QueryClientProvider>
          <GatewayOfflineFallback renderBanner>
            {children}
          </GatewayOfflineFallback>
          <Toaster position="top-center" />
        </QueryClientProvider>
      );
    case "config_error":
      throw new Error(result.message);
    default:
      assertNever(result);
  }
}
