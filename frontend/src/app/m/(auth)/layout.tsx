import { redirect } from "next/navigation";
import { type ReactNode } from "react";

import { GatewayOfflineFallback } from "@/components/workspace/gateway-offline-fallback";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { getServerSideUser } from "@/core/auth/server";
import { assertNever } from "@/core/auth/types";
import { getI18n } from "@/core/i18n/server";

export const dynamic = "force-dynamic";

/**
 * Server-side guard for the mobile auth screens, mirroring the desktop
 * `app/(auth)/layout.tsx` branch for branch — including the unconditional
 * `authenticated` redirect. The two trees must not disagree about who may see a
 * sign-in form.
 *
 * Redirect targets are the *public* paths (`/workspace`), never `/m/*`: the
 * middleware re-lands the next request on the mobile tree, which is what keeps
 * `/m/` out of the address bar.
 */
export default async function MobileAuthLayout({
  children,
}: {
  children: ReactNode;
}) {
  const result = await getServerSideUser();
  const { t } = await getI18n();

  // The gateway being unreachable still renders the screens — it only swaps in
  // the self-probing provider and adds a notice, where the desktop layout would
  // replace the page with a banner.
  if (result.tag === "gateway_unavailable") {
    return (
      <GatewayOfflineFallback>
        <MobileAuthScreen notice={t.workspace.gatewayUnavailable}>
          {children}
        </MobileAuthScreen>
      </GatewayOfflineFallback>
    );
  }

  switch (result.tag) {
    case "authenticated":
      // `redirect` returns `never`, so this arm falls through only in the type
      // system — same shape as the desktop layout.
      redirect("/workspace");
    case "needs_setup":
      // Signed in but still owes a password change — that screen needs the user.
      return (
        <MobileAuthScreen>
          <AuthProvider initialUser={result.user}>{children}</AuthProvider>
        </MobileAuthScreen>
      );
    case "system_setup_required":
    case "unauthenticated":
      return (
        <MobileAuthScreen>
          <AuthProvider initialUser={null}>{children}</AuthProvider>
        </MobileAuthScreen>
      );
    case "config_error":
      throw new Error(result.message);
    default:
      assertNever(result);
  }
}

/**
 * Full-height column shared by the three auth screens (prototype ⑧).
 *
 * Supplies its own scrolling `<main>`: `app/m/layout.tsx` stopped providing one
 * when the tab bar moved into `(app)`, and this branch is the one that must not
 * have one — the bar is what normally owns the bottom of the screen. The inner
 * `min-h-full` rather than a viewport unit keeps "fill the visible height"
 * tracking the address-bar collapse that the root `100dvh` exists to handle.
 *
 * Both safe-area insets are owned here: the tab bar normally carries the bottom
 * one, and these are the only mobile routes without it.
 */
function MobileAuthScreen({
  children,
  notice,
}: {
  children: ReactNode;
  notice?: string;
}) {
  return (
    <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
      <div className="flex min-h-full flex-col px-6 pt-[calc(env(safe-area-inset-top)+2rem)] pb-[calc(env(safe-area-inset-bottom)+1.5rem)]">
        {notice && (
          <p
            role="status"
            className="bg-muted text-muted-foreground mb-4 rounded-xl px-3 py-2 text-sm"
          >
            {notice}
          </p>
        )}
        <div className="flex flex-1 flex-col justify-center">{children}</div>
      </div>
    </main>
  );
}
