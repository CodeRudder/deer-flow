export interface ImageGenerationModel {
  name: string;
  display_name: string;
}

export interface ImageGenerationProvider {
  name: string;
  display_name: string;
  configured: boolean;
  models: ImageGenerationModel[];
}

export interface ImageGenerationProvidersResponse {
  skill_enabled: boolean;
  providers: ImageGenerationProvider[];
}
