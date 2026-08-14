import { getBackendBaseURL } from "../config";

import type { VideoGenerationProvidersResponse } from "./types";

export async function loadVideoGenerationProviders() {
  const res = await fetch(
    `${getBackendBaseURL()}/api/video-generation/providers`,
  );
  if (!res.ok) {
    throw new Error("Failed to load video generation providers");
  }
  return (await res.json()) as VideoGenerationProvidersResponse;
}
