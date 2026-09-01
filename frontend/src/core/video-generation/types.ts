export interface VideoResolutionRate {
  resolution: string;
  yuan_per_second_min: number;
  yuan_per_second_max: number;
}

/** 用户侧价目摘要（后端由管理员费率规则推导，模型名小写匹配）。 */
export interface VideoModelBilling {
  resolutions: VideoResolutionRate[];
  min_duration_seconds?: number | null;
  max_duration_seconds?: number | null;
}

export interface VideoGenerationModel {
  name: string;
  display_name: string;
  description?: string | null;
  billing?: VideoModelBilling | null;
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
