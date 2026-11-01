import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { QuotaMeResponse } from "./types";

/** 查询当前登录用户的额度（配了的维度才返回，含仅记录态）。 */
export async function fetchQuotaMe(): Promise<QuotaMeResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/quotas/me`);
  if (!response.ok) {
    throw new Error(`Failed to load quota (${response.status})`);
  }
  return (await response.json()) as QuotaMeResponse;
}
