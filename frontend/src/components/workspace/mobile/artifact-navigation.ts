import { isWriteFileArtifact } from "@/core/artifacts/preview";
import {
  canBrowserPreviewFile,
  checkCodeFile,
  getFileName,
  isImageFile,
} from "@/core/utils/files";

/**
 * Pure helpers for the mobile artifact screen (T6, prototype ⑤).
 *
 * Everything here is a function of its arguments only, which is what makes the
 * screen's two fiddly parts testable without a browser: turning an artifact
 * identifier into a URL (and back), and deciding which renderer it gets.
 */

/** Public prefix of the chat tree. `/m/` never appears in a link (plan §1.2.1). */
export const ARTIFACTS_BASE_PATH = "/workspace";

/**
 * Public URL of the full-screen artifact screen.
 *
 * The whole artifact identifier — which for an in-transcript `write_file` step
 * is the synthetic `write-file:/path?message_id=…&tool_call_id=…` URL — goes
 * into a single path segment. It has to be one segment: the identifier
 * contains `/` and `?`, and letting either through would change the shape of
 * the URL (an extra segment, a query string) rather than name the artifact.
 *
 * Dots are deliberately left readable (`encodeURIComponent` does not escape
 * them) — the middleware matches this route explicitly for exactly that
 * reason, see `src/middleware.ts`.
 */
export function artifactHref(
  threadId: string,
  filepath: string,
  { isMock = false }: { isMock?: boolean } = {},
): string {
  const path = `${ARTIFACTS_BASE_PATH}/chats/${threadId}/artifacts/${encodeURIComponent(filepath)}`;
  return isMock ? `${path}?mock=true` : path;
}

/**
 * The `[path]` route param back to the artifact identifier.
 *
 * Whether Next.js hands over the raw or the decoded param is a router detail,
 * so this tolerates both: a second decode of an already-decoded value is a
 * no-op unless the value contains a literal `%`, in which case the decode
 * throws and the value is returned untouched.
 */
export function decodeArtifactParam(param: string): string {
  try {
    return decodeURIComponent(param);
  } catch {
    return param;
  }
}

/** The file path a `write-file:` identifier points at, or `undefined`. */
export function writeFilePath(filepath: string): string | undefined {
  if (!isWriteFileArtifact(filepath)) {
    return undefined;
  }
  try {
    return decodeURIComponent(new URL(filepath).pathname);
  } catch {
    return undefined;
  }
}

/**
 * The artifact's own path — the `write-file:` wrapper stripped, so the
 * extension checks below see the real file.
 */
export function resolvedArtifactPath(filepath: string): string {
  return writeFilePath(filepath) ?? filepath;
}

/** Basename shown in the header; for `write-file:` identifiers, of the target. */
export function artifactDisplayName(filepath: string): string {
  const path = resolvedArtifactPath(filepath);
  // A `write-file:` URL that does not parse has no pathname to split; falling
  // back to the raw string keeps a label on screen instead of throwing.
  return path ? getFileName(path) : filepath;
}

/** Whether the artifact can be fetched from the backend at all. */
export function isDownloadableArtifact(filepath: string): boolean {
  return !isWriteFileArtifact(filepath);
}

/**
 * How the screen renders an artifact (prototype ⑤).
 *
 * Mirrors `ArtifactFileDetail`'s dispatch so the two trees agree on what each
 * file is: images first (an `.svg` is an image before it is markup), then the
 * two markup languages that have a rendered form, then code, then whatever the
 * browser can still display itself, and finally the download-only fallback.
 */
export type ArtifactViewMode =
  | "image"
  | "html"
  | "markdown"
  | "code"
  | "iframe"
  | "download";

export function artifactViewMode(filepath: string): ArtifactViewMode {
  const path = resolvedArtifactPath(filepath);
  if (isImageFile(path)) {
    return "image";
  }
  const { isCodeFile, language } = checkCodeFile(path);
  if (isCodeFile) {
    if (language === "html") {
      return "html";
    }
    if (language === "markdown") {
      return "markdown";
    }
    return "code";
  }
  return canBrowserPreviewFile(path) ? "iframe" : "download";
}
