import { useQuery } from "@tanstack/react-query";

import { loadModels } from "./api";

export function useModels({ enabled = true }: { enabled?: boolean } = {}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["models"],
    queryFn: () => loadModels(),
    enabled,
    refetchOnWindowFocus: false,
  });
  return {
    models: data?.models ?? [],
    visionModels: data?.vision_models ?? [],
    tokenUsageEnabled: data?.token_usage.enabled ?? false,
    isLoading,
    error,
  };
}
