import { fetch as fetchWithAuth } from "@/core/api/fetcher";

import { getBackendBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";

import type {
  ManagedModel,
  ManagedModelsResponse,
  ManagedModelWrite,
  ModelProvider,
  ModelProvidersResponse,
  ModelTestResult,
  ModelsResponse,
  ThinkingProbeResult,
} from "./types";

/**
 * Error thrown by the model-config admin client.
 *
 * Carries the HTTP status so the settings UI can distinguish "you are not an
 * admin" (403) from validation failures (400/409/404) and render the
 * appropriate empty/error state.
 */
export class ModelConfigRequestError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ModelConfigRequestError";
    this.status = status;
  }
  get isAdminRequired(): boolean {
    return this.status === 403;
  }
}

async function readErrorDetail(
  response: Response,
  fallback: string,
): Promise<string> {
  const error = (await response.json().catch(() => ({}))) as {
    detail?: unknown;
  };
  return typeof error.detail === "string" ? error.detail : fallback;
}

/**
 * Send a request to the model-config admin API and unwrap `{ models }`.
 *
 * Always goes through the CSRF-injecting `fetch` wrapper from
 * `@/core/api/fetcher` — the gateway's CSRF middleware 403s every
 * state-changing method that lacks the `X-CSRF-Token` header.
 */
async function requestModels(
  path: string,
  init: RequestInit | undefined,
  fallback: string,
): Promise<ManagedModel[]> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/models${path}`,
    init,
  );
  if (!response.ok) {
    throw new ModelConfigRequestError(
      response.status,
      await readErrorDetail(response, fallback),
    );
  }
  const data = (await response.json()) as ManagedModelsResponse;
  return data.models;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/** Read the whole managed model region. */
export async function loadManagedModels(): Promise<ManagedModel[]> {
  return requestModels("/config", undefined, "Failed to load models");
}

/**
 * Read the provider presets the model form renders as a dropdown.
 *
 * Admin-only, like the rest of the model-config endpoints — the page only calls
 * it once the managed list has loaded, which already implies admin.
 */
export async function loadModelProviders(): Promise<ModelProvider[]> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/models/providers`,
  );
  if (!response.ok) {
    throw new ModelConfigRequestError(
      response.status,
      await readErrorDetail(response, "Failed to load providers"),
    );
  }
  const data = (await response.json()) as ModelProvidersResponse;
  return data.providers;
}

/** Replace the whole managed model region in one write. */
export async function replaceManagedModels(
  models: ManagedModelWrite[],
): Promise<ManagedModel[]> {
  return requestModels(
    "/config",
    jsonInit("PUT", { models }),
    "Failed to update models",
  );
}

/** Append a new model entry. */
export async function createManagedModel(
  model: ManagedModelWrite,
): Promise<ManagedModel[]> {
  return requestModels("/", jsonInit("POST", model), "Failed to create model");
}

/** Overwrite the model entry addressed by `name`. */
export async function updateManagedModel(
  name: string,
  model: ManagedModelWrite,
): Promise<ManagedModel[]> {
  return requestModels(
    `/${encodeURIComponent(name)}`,
    jsonInit("PUT", model),
    "Failed to update model",
  );
}

/** Remove the model entry addressed by `name`. */
export async function deleteManagedModel(
  name: string,
): Promise<ManagedModel[]> {
  return requestModels(
    `/${encodeURIComponent(name)}`,
    { method: "DELETE" },
    "Failed to delete model",
  );
}

/** Probe connectivity for a candidate model entry without persisting it. */
export async function testManagedModel(
  model: ManagedModelWrite,
): Promise<ModelTestResult> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/models/test`,
    jsonInit("POST", model),
  );
  if (!response.ok) {
    throw new ModelConfigRequestError(
      response.status,
      await readErrorDetail(response, "Failed to test model"),
    );
  }
  return response.json() as Promise<ModelTestResult>;
}

const STATIC_MODELS_RESPONSE: ModelsResponse = {
  models: [],
  vision_models: [],
  token_usage: { enabled: false },
};

/**
 * Ask an endpoint what it actually does with thinking.
 *
 * Distinct from `testManagedModel`: that one asks "does it connect", this one
 * asks "does reasoning happen, and can it be turned off". A failed connection
 * here is still HTTP 200 with `ok: false` — a normal answer, not a server fault.
 */
export async function probeManagedModelThinking(
  model: ManagedModelWrite,
): Promise<ThinkingProbeResult> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/models/probe-thinking`,
    jsonInit("POST", model),
  );
  if (!response.ok) {
    throw new ModelConfigRequestError(
      response.status,
      await readErrorDetail(response, "Failed to probe thinking"),
    );
  }
  return (await response.json()) as ThinkingProbeResult;
}

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
