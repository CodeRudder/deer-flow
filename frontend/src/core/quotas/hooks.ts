import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { fetchQuotaMe } from "./api";

export const QUOTA_ME_QUERY_KEY = ["quotas", "me"] as const;

export function useQuotaMe() {
  return useQuery({
    queryKey: QUOTA_ME_QUERY_KEY,
    queryFn: fetchQuotaMe,
    staleTime: 30_000,
    // 显式声明：全局 QueryClient 为裸默认（true），防未来全局改 false 静默失效
    refetchOnWindowFocus: true,
    // 失败 = 指示器静默隐藏，不重试打扰
    retry: false,
  });
}

/** thread 流从运行中转空闲时失效余额缓存（生成结算后余额立即可见）。 */
export function useInvalidateQuotaOnRunEnd(isThreadBusy: boolean): void {
  const queryClient = useQueryClient();
  const wasBusy = useRef(isThreadBusy);

  useEffect(() => {
    if (wasBusy.current && !isThreadBusy) {
      void queryClient.invalidateQueries({ queryKey: QUOTA_ME_QUERY_KEY });
    }
    wasBusy.current = isThreadBusy;
  }, [isThreadBusy, queryClient]);
}
