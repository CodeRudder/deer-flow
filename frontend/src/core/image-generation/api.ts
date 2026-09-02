import { getBackendBaseURL } from "../config";

import type { ImageGenerationProvidersResponse } from "./types";

export async function loadImageGenerationProviders() {
  const res = await fetch(
    `${getBackendBaseURL()}/api/image-generation/providers`,
  );
  if (!res.ok) {
    throw new Error("Failed to load image generation providers");
  }
  return (await res.json()) as ImageGenerationProvidersResponse;
}
