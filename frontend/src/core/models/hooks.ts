import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ModelConfigRequestError,
  createManagedModel,
  deleteManagedModel,
  loadManagedModels,
  loadModelProviders,
  loadModels,
  probeManagedModelThinking,
  testManagedModel,
  updateManagedModel,
} from "./api";
import type { ManagedModelWrite } from "./types";

/** Query key for the managed model region (`GET /api/models/config`). */
export const MANAGED_MODELS_QUERY_KEY = ["managedModels"] as const;

/** Query key for the provider presets (`GET /api/models/providers`). */
export const MODEL_PROVIDERS_QUERY_KEY = ["modelProviders"] as const;

/**
 * Query key of the public model list the chat model picker reads.
 *
 * Shared with `useModels` below — a write here must invalidate it too, or the
 * picker keeps serving the pre-edit list until a reload.
 */
export const MODELS_QUERY_KEY = ["models"] as const;

export function useModels({ enabled = true }: { enabled?: boolean } = {}) {
  const { data, isLoading, error } = useQuery({
    queryKey: MODELS_QUERY_KEY,
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

export function useManagedModels({
  enabled = true,
}: { enabled?: boolean } = {}) {
  const { data, isLoading, error } = useQuery({
    queryKey: MANAGED_MODELS_QUERY_KEY,
    queryFn: () => loadManagedModels(),
    enabled,
    retry: (count, error) =>
      !(error instanceof ModelConfigRequestError) && count < 3,
  });
  return { models: data ?? [], isLoading, error };
}

export function useModelProviders({
  enabled = true,
}: { enabled?: boolean } = {}) {
  const { data, isLoading, error } = useQuery({
    queryKey: MODEL_PROVIDERS_QUERY_KEY,
    queryFn: () => loadModelProviders(),
    enabled,
    // The endpoint is admin-only, so a 403 is a stable answer — retrying it
    // three times only delays the error state. Same rule as `useManagedModels`.
    retry: (count, error) =>
      !(error instanceof ModelConfigRequestError) && count < 3,
    // The preset table is static apart from installed packages; a focus refetch
    // buys nothing.
    refetchOnWindowFocus: false,
  });
  return { providers: data ?? [], isLoading, error };
}

/**
 * Invalidate both the managed list and the picker's model list after a write.
 *
 * The picker reads `["models"]`, so invalidating only the managed key leaves it
 * stale.
 */
function useInvalidateModelConfig() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: MANAGED_MODELS_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: MODELS_QUERY_KEY }),
    ]);
}

/**
 * Create or update a managed model.
 *
 * Passing a `name` updates the entry addressed by it (`PUT /{name}`);
 * omitting it appends a new entry (`POST /`).
 */
export function useSaveManagedModel() {
  const invalidate = useInvalidateModelConfig();
  return useMutation({
    mutationFn: ({
      name,
      model,
    }: {
      name?: string;
      model: ManagedModelWrite;
    }) => (name ? updateManagedModel(name, model) : createManagedModel(model)),
    onSuccess: () => {
      void invalidate();
    },
  });
}

export function useDeleteManagedModel() {
  const invalidate = useInvalidateModelConfig();
  return useMutation({
    mutationFn: (name: string) => deleteManagedModel(name),
    onSuccess: () => {
      void invalidate();
    },
  });
}

/** Probe a candidate entry for connectivity. Writes nothing — no invalidation. */
export function useTestManagedModel() {
  return useMutation({
    mutationFn: (model: ManagedModelWrite) => testManagedModel(model),
  });
}

/**
 * Observe what an endpoint does with thinking.
 *
 * Like `useTestManagedModel`, this writes nothing, so it invalidates no query.
 */
export function useProbeManagedModelThinking() {
  return useMutation({
    mutationFn: (model: ManagedModelWrite) =>
      probeManagedModelThinking(model),
  });
}
