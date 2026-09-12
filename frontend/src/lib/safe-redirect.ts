/**
 * Open-redirect guard for the `?next=` parameter consumed by the auth pages.
 *
 * The value arrives from the query string, so it is attacker-controlled: a
 * login page that navigates to whatever `next` says is a phishing hop
 * (`/login?next=https://evil.example`). Only same-origin *relative paths*
 * survive.
 *
 * Deliberately stricter than the desktop login page's inline `validateNextParam`
 * — that one lets a colon through for `/`-prefixed values (`/a:b`), which this
 * rejects outright. Real `next` values are workspace paths such as
 * `/workspace/chats/<uuid>` and never carry a colon, so the stricter rule costs
 * nothing while closing the `javascript:` / `data:` shapes at the source.
 */
export function isSafeRelativePath(
  next: string | null | undefined,
): boolean {
  if (!next) {
    return false;
  }

  // Must be a relative path — "//host" and "https://host" are protocol-relative
  // or absolute URLs, and anything not starting with "/" is a relative-path
  // escape (e.g. "../../evil").
  if (!next.startsWith("/") || next.startsWith("//")) {
    return false;
  }

  // "javascript:", "data:", and every other scheme separator.
  return !next.includes(":");
}

/**
 * Resolve the validated redirect target, falling back to the workspace when
 * `next` is missing or unsafe.
 */
export function resolveSafeRedirect(
  next: string | null | undefined,
  fallback = "/workspace",
): string {
  return isSafeRelativePath(next) ? next! : fallback;
}
