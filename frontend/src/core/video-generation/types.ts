export interface VideoGenerationModel {
  name: string;
  display_name: string;
  description?: string | null;
}

export interface VideoGenerationProvider {
  name: string;
  display_name: string;
  configured: boolean;
  models: VideoGenerationModel[];
}

export interface VideoGenerationProvidersResponse {
  skill_enabled: boolean;
  providers: VideoGenerationProvider[];
}
