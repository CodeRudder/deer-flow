import { getBackendBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";

import type { ModelsResponse } from "./types";

const STATIC_MODELS_RESPONSE: ModelsResponse = {
  models: [],
  vision_models: [],
  token_usage: { enabled: false },
};

export async function loadModels(): Promise<ModelsResponse> {
  if (isStaticWebsiteOnly()) {
    return STATIC_MODELS_RESPONSE;
  }

  const res = await fetch(`${getBackendBaseURL()}/api/models`);
  const data = (await res.json()) as Partial<ModelsResponse>;
  return {
    models: data.models ?? [],
    vision_models: data.vision_models ?? [],
    token_usage: data.token_usage ?? { enabled: false },
  };
}
