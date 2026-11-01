/**
 * Tests for the model-config admin API client (`src/core/models/api.ts`).
 *
 * The admin endpoints live under `/api/models`, are admin-only (403 without
 * admin privileges) and go through the CSRF-injecting `fetch` wrapper from
 * `@/core/api/fetcher` — the gateway's CSRF middleware 403s every write that
 * skips the `X-CSRF-Token` header, which would silently break save/delete.
 *
 * These tests pin:
 *  - method + URL for each endpoint (path params are URL-encoded),
 *  - writes go through the wrapper, never `globalThis.fetch`,
 *  - non-2xx bodies surface as `ModelConfigRequestError` (403 →
 *    `isAdminRequired`, 400 → the backend `detail` message, 409/404 → status),
 *  - a masked `api_key` is forwarded verbatim (round-trips the stored secret)
 *    and an omitted `api_key` stays omitted.
 */
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  test,
  rs,
} from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  ModelConfigRequestError,
  createManagedModel,
  deleteManagedModel,
  loadManagedModels,
  replaceManagedModels,
  testManagedModel,
  updateManagedModel,
} from "@/core/models/api";
import type { ManagedModelWrite } from "@/core/models/types";

const mockedFetch = rs.mocked(fetcher);

const BASE_MODEL: ManagedModelWrite = {
  name: "gpt-4o",
  model: "gpt-4o",
  use: "langchain_openai:ChatOpenAI",
};

const MANAGED_MODEL = {
  index: 0,
  name: "gpt-4o",
  model: "gpt-4o",
  use: "langchain_openai:ChatOpenAI",
  api_key_masked: "sk-****abcd",
};

let globalFetch: ReturnType<typeof rs.fn>;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function lastCall() {
  const call = mockedFetch.mock.calls.at(-1);
  if (!call) throw new Error("fetcher.fetch was never called");
  return { url: call[0] as string, init: call[1] };
}

beforeEach(() => {
  mockedFetch.mockReset();
  globalFetch = rs.fn();
  rs.stubGlobal("fetch", globalFetch);
});

afterEach(() => {
  rs.unstubAllGlobals();
});

describe("loadManagedModels", () => {
  test("GETs /api/models/config and unwraps `models`", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { models: [MANAGED_MODEL] }),
    );

    await expect(loadManagedModels()).resolves.toEqual([MANAGED_MODEL]);

    const { url, init } = lastCall();
    expect(url).toBe("/api/models/config");
    expect(init?.method ?? "GET").toBe("GET");
  });

  test("403 throws ModelConfigRequestError with isAdminRequired", async () => {
    mockedFetch.mockResolvedValue(
      jsonResponse(403, { detail: "Admin privileges required." }),
    );

    await expect(loadManagedModels()).rejects.toMatchObject({
      name: "ModelConfigRequestError",
      status: 403,
      isAdminRequired: true,
      message: "Admin privileges required.",
    });
    await expect(loadManagedModels()).rejects.toBeInstanceOf(
      ModelConfigRequestError,
    );
  });

  test("does not touch globalThis.fetch", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { models: [] }));

    await loadManagedModels();

    expect(globalFetch).not.toHaveBeenCalled();
  });
});

describe("replaceManagedModels", () => {
  test("PUTs /api/models/config with the wrapped body", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { models: [MANAGED_MODEL] }),
    );

    await expect(replaceManagedModels([BASE_MODEL])).resolves.toEqual([
      MANAGED_MODEL,
    ]);

    const { url, init } = lastCall();
    expect(url).toBe("/api/models/config");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(init?.body as string)).toEqual({
      models: [BASE_MODEL],
    });
  });
});

describe("createManagedModel", () => {
  test("POSTs /api/models/ with the raw model body", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { models: [MANAGED_MODEL] }),
    );

    await createManagedModel(BASE_MODEL);

    const { url, init } = lastCall();
    expect(url).toBe("/api/models/");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual(BASE_MODEL);
  });

  test("409 (duplicate name) maps to ModelConfigRequestError", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(409, { detail: "Model 'gpt-4o' is already defined." }),
    );

    await expect(createManagedModel(BASE_MODEL)).rejects.toMatchObject({
      name: "ModelConfigRequestError",
      status: 409,
      isAdminRequired: false,
      message: "Model 'gpt-4o' is already defined.",
    });
  });
});

describe("updateManagedModel", () => {
  test("PUTs /api/models/{name}", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { models: [MANAGED_MODEL] }),
    );

    await updateManagedModel("gpt-4o", BASE_MODEL);

    const { url, init } = lastCall();
    expect(url).toBe("/api/models/gpt-4o");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(init?.body as string)).toEqual(BASE_MODEL);
  });

  test("URL-encodes the name path param", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { models: [] }));

    await updateManagedModel("my model/v2", BASE_MODEL);

    expect(lastCall().url).toBe("/api/models/my%20model%2Fv2");
  });

  test("404 (unknown name) maps to ModelConfigRequestError", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(404, { detail: "Unknown model 'nope'." }),
    );

    await expect(updateManagedModel("nope", BASE_MODEL)).rejects.toMatchObject({
      name: "ModelConfigRequestError",
      status: 404,
      message: "Unknown model 'nope'.",
    });
  });

  test("forwards a masked api_key verbatim so the backend keeps the secret", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { models: [] }));

    await updateManagedModel("gpt-4o", {
      ...BASE_MODEL,
      api_key: "sk-****abcd",
    });

    const body = JSON.parse(lastCall().init?.body as string);
    expect(body.api_key).toBe("sk-****abcd");
    expect(body).not.toHaveProperty("api_key_value");
  });

  test("omits api_key when the caller does not send one", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { models: [] }));

    await updateManagedModel("gpt-4o", BASE_MODEL);

    const body = JSON.parse(lastCall().init?.body as string);
    expect(body).not.toHaveProperty("api_key");
  });

  test("400 surfaces the backend detail message", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(400, { detail: "models[0].use is required" }),
    );

    await expect(
      updateManagedModel("gpt-4o", BASE_MODEL),
    ).rejects.toMatchObject({
      name: "ModelConfigRequestError",
      status: 400,
      isAdminRequired: false,
      message: "models[0].use is required",
    });
  });

  test("falls back to a generic message when detail is not a string", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response("", { status: 500, statusText: "Internal Server Error" }),
    );

    await expect(
      updateManagedModel("gpt-4o", BASE_MODEL),
    ).rejects.toMatchObject({
      name: "ModelConfigRequestError",
      status: 500,
      message: "Failed to update model",
    });
  });
});

describe("deleteManagedModel", () => {
  test("DELETEs /api/models/{name}", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { models: [] }));

    await deleteManagedModel("gpt-4o");

    const { url, init } = lastCall();
    expect(url).toBe("/api/models/gpt-4o");
    expect(init?.method).toBe("DELETE");
  });

  test("403 throws ModelConfigRequestError with isAdminRequired", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(403, { detail: "Admin privileges required." }),
    );

    await expect(deleteManagedModel("gpt-4o")).rejects.toMatchObject({
      status: 403,
      isAdminRequired: true,
    });
  });
});

describe("testManagedModel", () => {
  test("POSTs /api/models/test and returns the probe result", async () => {
    const result = { ok: true, latency_ms: 123, error: null };
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, result));

    await expect(testManagedModel(BASE_MODEL)).resolves.toEqual(result);

    const { url, init } = lastCall();
    expect(url).toBe("/api/models/test");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual(BASE_MODEL);
  });

  test("does not touch globalThis.fetch (CSRF wrapper required)", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { ok: false, latency_ms: 0, error: "boom" }),
    );

    await testManagedModel(BASE_MODEL);

    expect(globalFetch).not.toHaveBeenCalled();
  });
});

describe("every write goes through the CSRF-injecting wrapper", () => {
  test("writes call @/core/api/fetcher", async () => {
    mockedFetch.mockImplementation(async () =>
      jsonResponse(200, { models: [] }),
    );

    await replaceManagedModels([BASE_MODEL]);
    await createManagedModel(BASE_MODEL);
    await updateManagedModel("gpt-4o", BASE_MODEL);
    await deleteManagedModel("gpt-4o");

    expect(mockedFetch).toHaveBeenCalledTimes(4);
    expect(globalFetch).not.toHaveBeenCalled();
  });
});
