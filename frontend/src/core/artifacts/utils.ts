import { getBackendBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";
import type { AgentThread } from "../threads";

export function urlOfArtifact({
  filepath,
  threadId,
  download = false,
  isMock = false,
}: {
  filepath: string;
  threadId: string;
  download?: boolean;
  isMock?: boolean;
}) {
  if (isStaticWebsiteOnly()) {
    return staticDemoArtifactURL({ filepath, threadId, download });
  }
  if (isMock) {
    return `${getBackendBaseURL()}/mock/api/threads/${threadId}/artifacts${filepath}${download ? "?download=true" : ""}`;
  }
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${filepath}${download ? "?download=true" : ""}`;
}

export function extractArtifactsFromThread(thread: AgentThread) {
  return thread.values.artifacts ?? [];
}

// Scaled preview variant: the backend serves a downscaled JPEG for image
// artifacts when `w`/`fmt` query params are present (see the artifacts
// router), while the parameter-less URL keeps returning the original bytes.
const ARTIFACT_PREVIEW_IMAGE_QUERY = "w=720&fmt=jpg";

export function withArtifactPreviewImage(url: string): string {
  if (!url) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${ARTIFACT_PREVIEW_IMAGE_QUERY}`;
}

export function isArtifactImageURL(url: string): boolean {
  return /\/api\/threads\/[^/]+\/artifacts\//.test(url);
}

/**
 * Image element source for an artifact URL: the 720p JPEG variant when the
 * URL points at an image artifact served by the backend, the URL unchanged
 * otherwise (external images, uploads endpoints, static demo assets).
 */
export function artifactImageSrc(url: string): string {
  return isArtifactImageURL(url) ? withArtifactPreviewImage(url) : url;
}

export function resolveArtifactURL(absolutePath: string, threadId: string) {
  if (isStaticWebsiteOnly()) {
    return staticDemoArtifactURL({ filepath: absolutePath, threadId });
  }
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${absolutePath}`;
}

function staticDemoArtifactURL({
  filepath,
  threadId,
  download = false,
}: {
  filepath: string;
  threadId: string;
  download?: boolean;
}) {
  const demoPath = filepath.replace(/^\/mnt\//, "/");
  return `${getBackendBaseURL()}/demo/threads/${threadId}${demoPath}${download ? "?download=true" : ""}`;
}
