import { useQuery } from "@tanstack/react-query";

import { loadVideoGenerationProviders } from "./api";

export function useVideoGenerationProviders({
  enabled = true,
}: { enabled?: boolean } = {}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["video-generation", "providers"],
    queryFn: () => loadVideoGenerationProviders(),
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
