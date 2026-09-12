/**
 * In-app chat route construction.
 *
 * The chat pages navigate client-side: `history.replaceState` once the backend
 * creates a thread, and `router.replace` when a thread turns out to be gone.
 * Those calls are made from the page, so the prefix cannot be baked in here —
 * it is a parameter, and the caller passes the prefix of the tree it is served
 * from.
 *
 * The value must be the route tree's *public* prefix. A tree that is served
 * under `/m/*` by an internal rewrite (`src/middleware.ts`) still sits at
 * `/workspace/*` in the address bar, so it uses the default; only a tree that
 * is genuinely published at `/m/*` would override it.
 */

/** Public prefix of the default (desktop) chat tree. */
export const DEFAULT_CHAT_BASE_PATH = "/workspace";

/** Tolerate `workspace/`, `/workspace/` and `/workspace` alike. */
function normalizeBasePath(basePath: string): string {
  const trimmed = basePath.trim().replace(/\/+$/, "");
  if (!trimmed) {
    return "";
  }
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

/**
 * Build a chat route under `basePath`.
 *
 * @param basePath public prefix of the hosting tree, e.g. `/workspace`.
 * @param threadId thread to open; omit for the new-chat route.
 */
export function chatPath(basePath: string, threadId?: string): string {
  const base = normalizeBasePath(basePath);
  return threadId ? `${base}/chats/${threadId}` : `${base}/chats/new`;
}
