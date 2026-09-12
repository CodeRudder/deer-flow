export interface Model {
  id: string;
  name: string;
  model: string;
  display_name: string;
  description?: string | null;
  supports_thinking?: boolean;
  supports_reasoning_effort?: boolean;
}

export interface TokenUsageSettings {
  enabled: boolean;
}

export interface VisionModel {
  id: string;
  name: string;
  model: string;
  display_name?: string | null;
  is_default?: boolean;
}

export interface ModelsResponse {
  models: Model[];
  vision_models: VisionModel[];
  token_usage: TokenUsageSettings;
}

/**
 * A model entry inside the managed region of `config.yaml`, as returned by the
 * admin `/api/models` endpoints. `index` is its position in that region and
 * `name` is the unique id used to address `PUT`/`DELETE`.
 *
 * Provider-specific extras are passed through untouched.
 */
export interface ManagedModel {
  index: number;
  name: string;
  model: string;
  display_name?: string | null;
  description?: string | null;
  /** Provider class path, e.g. `"langchain_openai:ChatOpenAI"`. */
  use: string;
  /** Never the cleartext — a masked placeholder or a boolean. */
  api_key_masked?: string | boolean | null;
  supports_thinking?: boolean;
  supports_reasoning_effort?: boolean;
  supports_vision?: boolean;
  [k: string]: unknown;
}

/**
 * Write shape for a managed model: the read shape minus the server-owned
 * `index` / `api_key_masked`, plus the key fields.
 *
 * To keep an existing secret, send back the masked `api_key` (or omit the
 * field entirely) — the backend only rewrites the stored key when
 * `api_key_value` carries a cleartext.
 */
export interface ManagedModelWrite {
  name: string;
  model: string;
  display_name?: string | null;
  description?: string | null;
  use: string;
  /** Provider endpoint; `null` (or omitted) uses the provider/SDK default. */
  api_base?: string | null;
  supports_thinking?: boolean;
  supports_reasoning_effort?: boolean;
  supports_vision?: boolean;
  /** `$VAR` reference (e.g. `"$OPENAI_API_KEY"`) or a literal key. */
  api_key?: string;
  /** Write-only cleartext, persisted to `.env` (never to `config.yaml`). */
  api_key_value?: string;
  [k: string]: unknown;
}

/** Envelope returned by every managed-model mutation. */
export interface ManagedModelsResponse {
  models: ManagedModel[];
}

/**
 * A provider the model form offers by label, as returned by
 * `GET /api/models/providers`.
 *
 * `use` is the class path an entry using this provider carries — the form fills
 * it in silently, the operator never types it. `available` is the backend's
 * probe of that class path through `deerflow.reflection.resolve_class`: when the
 * provider's package is not installed the entry is `available: false` and
 * `reason` carries the resolver's install hint, so the dropdown can disable the
 * item and explain itself.
 */
export interface ModelProvider {
  key: string;
  label: string;
  use: string;
  /** Endpoint prefilled when the provider has one; `null` means the SDK default. */
  default_api_base: string | null;
  available: boolean;
  reason: string | null;
}

/** Envelope returned by `GET /api/models/providers`. */
export interface ModelProvidersResponse {
  providers: ModelProvider[];
}

/** Result of a connectivity probe against a candidate model entry. */
export interface ModelTestResult {
  ok: boolean;
  latency_ms: number;
  error: string | null;
}
