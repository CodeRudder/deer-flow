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
