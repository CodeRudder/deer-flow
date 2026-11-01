/**
 * Device classification for the `/m/*` split.
 *
 * The middleware decides the route tree from the request's User-Agent, so the
 * classification has to be a pure function of the UA string — no `window`,
 * no `navigator` — which is also what makes it unit-testable.
 *
 * Every unknown client resolves to `false` (desktop): a false positive would
 * hijack a desktop browser into the mobile tree, a false negative merely keeps
 * the page it has always rendered. Fail safe, never fail mobile.
 */

/**
 * Width at which the layout switches from the mobile shell to the desktop
 * pages. Tablets (768–1024px) intentionally reuse the desktop experience, so
 * the middleware only ever routes *phones* into `/m/*` — the UA cannot tell us
 * the viewport, but it can tell us "phone" vs "tablet".
 *
 * Shared with `@/hooks/use-mobile`, the client-side counterpart of this
 * boundary.
 */
export const MOBILE_BREAKPOINT = 768;

/** Crawlers and non-browser clients must never be routed into `/m/*`. */
const BOT_PATTERN =
  /bot|crawler|spider|crawl|slurp|facebookexternalhit|embedly|quora link preview|whatsapp|telegrambot|pinterest|vkshare|w3c_validator|headlesschrome|lighthouse|curl\/|wget\/|python-requests|httpclient/i;

/**
 * Tablets, checked *before* the mobile tokens: iPad Safari sends `Mobile/` in
 * its UA, so it would otherwise classify as a phone.
 */
const TABLET_PATTERN =
  /ipad|tablet|playbook|silk|kindle|xoom|nexus 7|nexus 9|nexus 10/i;

/**
 * Phones and other small-screen devices. `Mobi` is intentionally not listed —
 * `Mobile` already covers it, and `Mobile` is the token that distinguishes an
 * Android phone from an Android tablet.
 */
const MOBILE_PATTERN =
  /mobile|iphone|ipod|android|blackberry|bb10|iemobile|opera mini|opera mobi|webos|windows phone|windows ce|palm|symbian|fennec|maemo|midp|netfront|nitro/i;

/** Android ships one UA per form factor; only phones append `Mobile`. */
const ANDROID_PATTERN = /android/i;
const ANDROID_MOBILE_PATTERN = /mobile/i;

/**
 * Whether a request from this User-Agent should be served the `/m/*` tree.
 *
 * @param userAgent raw `User-Agent` header value (`null` when absent).
 */
export function isMobileUserAgent(
  userAgent: string | null | undefined,
): boolean {
  const ua = userAgent?.trim();
  if (!ua) {
    return false;
  }

  if (BOT_PATTERN.test(ua) || TABLET_PATTERN.test(ua)) {
    return false;
  }

  if (ANDROID_PATTERN.test(ua) && !ANDROID_MOBILE_PATTERN.test(ua)) {
    return false;
  }

  return MOBILE_PATTERN.test(ua);
}
