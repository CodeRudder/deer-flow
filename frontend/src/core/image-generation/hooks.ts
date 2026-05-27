import { useQuery } from "@tanstack/react-query";

import { loadImageGenerationProviders } from "./api";

export function useImageGenerationProviders({
  enabled = true,
}: { enabled?: boolean } = {}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["image-generation", "providers"],
    queryFn: () => loadImageGenerationProviders(),
    enabled,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  return {
    data,
    providers: data?.providers ?? [],
    isLoading,
    error,
  };
}
