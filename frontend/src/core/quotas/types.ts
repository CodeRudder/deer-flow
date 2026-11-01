export interface QuotaMetric {
  enforced: boolean;
  limit: number | null;
  used: number;
  /** 预占仅视频维度存在（生图按次无预占）。 */
  reserved?: number | null;
  remaining: number | null;
  ratio: number | null;
  status: string;
  unit: string;
}

export interface QuotaPeriod {
  period_type: string;
  label: string;
  period_start: string;
  period_end: string;
  timezone: string;
}

export interface QuotaScopeItem {
  scope_id: string;
  scope_code: string;
  scope_name: string;
  resource_type: string;
  period_type: string;
  period: QuotaPeriod;
  status: string;
  /** scope_default | temporary_override */
  source: string;
  requests: QuotaMetric | null;
  images: QuotaMetric | null;
  videos: QuotaMetric | null;
}

export interface QuotaMeResponse {
  user_id: string;
  status: string;
  items: QuotaScopeItem[];
}
