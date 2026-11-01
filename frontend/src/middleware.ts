import { type NextRequest, NextResponse } from "next/server";

import { isMobileUserAgent } from "@/lib/device";

/**
 * Mobile split: phones are rewritten onto the `/m/*` tree, everything else —
 * desktops, tablets, crawlers, unknown clients — is left completely untouched.
 *
 * The rewrite is internal, so the browser URL never changes: `/workspace`
 * stays `/workspace` in the address bar while rendering `/m/workspace`.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Already inside the mobile tree — never rewrite twice.
  if (pathname === "/m" || pathname.startsWith("/m/")) {
    return NextResponse.next();
  }

  if (!isMobileUserAgent(request.headers.get("user-agent"))) {
    return NextResponse.next();
  }

  if (isDesktopOnlyPath(pathname)) {
    return NextResponse.next();
  }

  const url = request.nextUrl.clone();
  url.pathname = `/m${pathname}`;
  return NextResponse.rewrite(url);
}

/**
 * Paths that keep the desktop page on phones.
 *
 * These are not "not ported yet" gaps but deliberate exclusions: the plan
 * scopes docs and blog out of the mobile work, whose locale-prefixed routes
 * (`/[lang]/docs`) are part of their URL rather than a middleware concern, and
 * `/` is the desktop landing/auth entry — there is no mobile root screen, so
 * rewriting it would trade a working page for a 404.
 */
const DESKTOP_ONLY_PREFIXES = ["/docs", "/en/docs", "/zh/docs", "/blog"];

function isDesktopOnlyPath(pathname: string): boolean {
  if (pathname === "/") {
    return true;
  }

  return DESKTOP_ONLY_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export const config = {
  /**
   * Skipped entirely: Next internals (`/_next`), backend proxies (`/api`),
   * the mock API used by the E2E suite (`/mock`), the mobile tree itself
   * (`/m`, so it can never be rewritten twice), and every static file that
   * carries an extension (`/favicon.ico`, `/logo.svg`, `/site.webmanifest`, …).
   */
  matcher: [
    "/((?!_next/|api/|mock/|m/|.*\\.[^/]+$).*)",
    /**
     * The mobile artifact viewer is the one route whose last segment is a
     * *file path*, so its URL routinely ends in an extension
     * (`…/artifacts/report.html`) and the static-file exclusion above would
     * skip it — leaving a phone on a desktop 404. It is a route, never a file,
     * so it is matched back in explicitly. The encoded path is a single
     * segment (`encodeURIComponent`), which `:path*` accepts.
     */
    "/workspace/chats/:thread_id/artifacts/:path*",
  ],
};
