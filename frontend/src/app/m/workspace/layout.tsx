import { redirect } from "next/navigation";
import { Toaster } from "sonner";

import { QueryClientProvider } from "@/components/query-client-provider";
import { GatewayOfflineFallback } from "@/components/workspace/gateway-offline-fallback";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { getServerSideUser } from "@/core/auth/server";
import { assertNever } from "@/core/auth/types";

export const dynamic = "force-dynamic";

/**
 * Guards and providers for the signed-in mobile tree.
 *
 * Mirrors `app/workspace/layout.tsx` branch for branch — the two trees serve
 * the same data and must not disagree about who may see it — but this is a
 * *nested* layout rather than one on `app/m/layout.tsx`: the tab-bar shell is
 * shared with the signed-out auth screens, which must render for an
 * unauthenticated visitor (T3). Providers belong here, next to the guard,
 * because `useInfiniteThreads()` needs a `QueryClient` and that only makes
 * sense once there is a session.
 *
 * Redirect targets are the *public* paths (`/login`, `/setup`, `/workspace`):
 * the middleware re-lands the next request on the mobile tree, which is what
 * keeps `/m/` out of the address bar.
 */
export default async function MobileWorkspaceLayout({
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
