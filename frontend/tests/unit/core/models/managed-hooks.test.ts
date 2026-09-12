/**
 * Tests for the model-config admin React Query hooks
 * (`src/core/models/hooks.ts`).
 *
 * The chat model picker reads the existing `["models"]` query, so every write
 * must invalidate BOTH the managed-models key and `["models"]` — otherwise the
 * picker keeps serving the pre-edit list. Connectivity tests write nothing and
 * must not invalidate at all.
 */
import { beforeEach, describe, expect, test, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  MANAGED_MODELS_QUERY_KEY,
  MODELS_QUERY_KEY,
  useDeleteManagedModel,
  useManagedModels,
  useSaveManagedModel,
  useTestManagedModel,
} from "@/core/models/hooks";
import type { ManagedModelWrite } from "@/core/models/types";

const mockedFetch = rs.mocked(fetcher);

const BASE_MODEL: ManagedModelWrite = {
  name: "gpt-4o",
  model: "gpt-4o",
  use: "langchain_openai:ChatOpenAI",
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** QueryClient whose `invalidateQueries` calls are recorded for assertions. */
function makeClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
  const invalidated: unknown[] = [];
  const original = client.invalidateQueries.bind(client);
  client.invalidateQueries = ((filters?: { queryKey?: unknown }) => {
    invalidated.push(filters?.queryKey);
    return original(filters as never);
  }) as typeof client.invalidateQueries;
  return { client, invalidated };
}

function wrapperFor(client: QueryClient) {
  const Wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
  Wrapper.displayName = "QueryClientWrapper";
  return Wrapper;
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("useManagedModels", () => {
  test("loads the managed list", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        models: [{ ...BASE_MODEL, index: 0, api_key_masked: "sk-****" }],
      }),
    );

    const { client } = makeClient();
    const { result } = renderHook(() => useManagedModels(), {
      wrapper: wrapperFor(client),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.models).toEqual([
      { ...BASE_MODEL, index: 0, api_key_masked: "sk-****" },
    ]);
  });

  test("does not retry on ModelConfigRequestError (403)", async () => {
    mockedFetch.mockResolvedValue(
      jsonResponse(403, { detail: "Admin privileges required." }),
    );

    const { client } = makeClient();
    const { result } = renderHook(() => useManagedModels(), {
      wrapper: wrapperFor(client),
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(mockedFetch).toHaveBeenCalledTimes(1);
  });

  test("retries non-typed errors up to 3 times", async () => {
    mockedFetch.mockRejectedValue(new Error("network down"));

    const { client } = makeClient();
    const { result } = renderHook(() => useManagedModels(), {
      wrapper: wrapperFor(client),
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(mockedFetch).toHaveBeenCalledTimes(4);
  });
});

describe("useSaveManagedModel", () => {
  test("POSTs when no name is given and invalidates both keys", async () => {
    mockedFetch.mockImplementation(async () =>
      jsonResponse(200, { models: [] }),
    );

    const { client, invalidated } = makeClient();
    const { result } = renderHook(() => useSaveManagedModel(), {
      wrapper: wrapperFor(client),
    });

    await act(async () => {
      await result.current.mutateAsync({ model: BASE_MODEL });
    });

    expect(mockedFetch.mock.calls.at(-1)?.[0]).toBe("/api/models/");
    expect(mockedFetch.mock.calls.at(-1)?.[1]?.method).toBe("POST");
    expect(invalidated).toEqual(
      expect.arrayContaining([MANAGED_MODELS_QUERY_KEY, MODELS_QUERY_KEY]),
    );
    expect(invalidated).toHaveLength(2);
  });

  test("PUTs when a name is given", async () => {
    mockedFetch.mockImplementation(async () =>
      jsonResponse(200, { models: [] }),
    );

    const { client } = makeClient();
    const { result } = renderHook(() => useSaveManagedModel(), {
      wrapper: wrapperFor(client),
    });

    await act(async () => {
      await result.current.mutateAsync({ name: "gpt-4o", model: BASE_MODEL });
    });

    expect(mockedFetch.mock.calls.at(-1)?.[0]).toBe("/api/models/gpt-4o");
    expect(mockedFetch.mock.calls.at(-1)?.[1]?.method).toBe("PUT");
  });
});

describe("useDeleteManagedModel", () => {
  test("DELETEs and invalidates the managed + picker keys", async () => {
    mockedFetch.mockImplementation(async () =>
      jsonResponse(200, { models: [] }),
    );

    const { client, invalidated } = makeClient();
    const { result } = renderHook(() => useDeleteManagedModel(), {
      wrapper: wrapperFor(client),
    });

    await act(async () => {
      await result.current.mutateAsync("gpt-4o");
    });

    expect(mockedFetch.mock.calls.at(-1)?.[0]).toBe("/api/models/gpt-4o");
    expect(mockedFetch.mock.calls.at(-1)?.[1]?.method).toBe("DELETE");
    expect(invalidated).toEqual(
      expect.arrayContaining([MANAGED_MODELS_QUERY_KEY, MODELS_QUERY_KEY]),
    );
  });
});

describe("useTestManagedModel", () => {
  test("POSTs /api/models/test and invalidates nothing", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { ok: true, latency_ms: 42, error: null }),
    );

    const { client, invalidated } = makeClient();
    const { result } = renderHook(() => useTestManagedModel(), {
      wrapper: wrapperFor(client),
    });

    await act(async () => {
      await expect(result.current.mutateAsync(BASE_MODEL)).resolves.toEqual({
        ok: true,
        latency_ms: 42,
        error: null,
      });
    });

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    expect(mockedFetch.mock.calls[0]?.[0]).toBe("/api/models/test");
    expect(mockedFetch.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(invalidated).toEqual([]);
  });
});
