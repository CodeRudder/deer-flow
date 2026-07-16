import { beforeEach, expect, test, rs } from "@rstest/core";

const fetchWithAuth = rs.fn();

rs.mock("@/core/api/fetcher", () => ({
  fetch: fetchWithAuth,
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

beforeEach(() => {
  fetchWithAuth.mockReset();
  fetchWithAuth.mockResolvedValue({
    ok: true,
    json: async () => ({ items: [], total: 0 }),
  });
});

test("custom usage range sends start and end query parameters", async () => {
  const { loadUsageSummary } = await import("@/core/admin/api");

  await loadUsageSummary({
    range: "custom",
    start: "2026-07-01",
    end: "2026-07-08",
  });

  const url = new URL(
    fetchWithAuth.mock.calls[0]![0] as string,
    "http://localhost:2026",
  );
  expect(url.pathname).toBe("/api/admin/usage/summary");
  expect(url.searchParams.get("range")).toBe("custom");
  expect(url.searchParams.get("start")).toBe("2026-07-01");
  expect(url.searchParams.get("end")).toBe("2026-07-08");
  expect(url.searchParams.has("from")).toBe(false);
});

test("user usage requests all three rankings with one limit", async () => {
  const { loadUsageUsers } = await import("@/core/admin/api");

  await loadUsageUsers({ range: "month", limit: 20 });

  const url = new URL(
    fetchWithAuth.mock.calls[0]![0] as string,
    "http://localhost:2026",
  );
  expect(url.pathname).toBe("/api/admin/usage/users");
  expect(url.searchParams.has("sort_by")).toBe(false);
  expect(url.searchParams.get("limit")).toBe("20");
});

test("quota list sends reference date, filter, search, and pagination", async () => {
  const { loadQuotaUsers } = await import("@/core/admin/api");

  await loadQuotaUsers({
    at: "2026-07-15",
    status: "exceeded",
    keyword: "user@example.com",
    page: 3,
    page_size: 50,
  });

  const url = new URL(
    fetchWithAuth.mock.calls[0]![0] as string,
    "http://localhost:2026",
  );
  expect(url.pathname).toBe("/api/admin/quotas/users");
  expect(url.searchParams.get("at")).toBe("2026-07-15");
  expect(url.searchParams.get("status")).toBe("exceeded");
  expect(url.searchParams.get("keyword")).toBe("user@example.com");
  expect(url.searchParams.get("page")).toBe("3");
  expect(url.searchParams.get("page_size")).toBe("50");
});

test("quota scope create keeps request policy and model rules", async () => {
  const { createQuotaScope } = await import("@/core/admin/api");

  await createQuotaScope({
    code: "claude_advanced",
    name: "Claude 高级模型",
    resource_type: "model",
    match_rules: { exact: ["claude-opus-4"], prefix: ["claude-"] },
    default_policy: {
      period_type: "weekly",
      requests: { enforced: true, limit: 5000 },
      images: null,
    },
    enabled: true,
  });

  const [url, init] = fetchWithAuth.mock.calls[0]! as [string, RequestInit];
  expect(url).toContain("/api/admin/quotas/scopes");
  expect(init.method).toBe("POST");
  expect(typeof init.body).toBe("string");
  const payload = JSON.parse(init.body as string);
  expect(payload.default_policy.requests).toEqual({
    enforced: true,
    limit: 5000,
  });
  expect(payload.match_rules.prefix).toEqual(["claude-"]);
});

test("quota scope create supports an administrator-created image range", async () => {
  const { createQuotaScope } = await import("@/core/admin/api");

  await createQuotaScope({
    code: "image_generation",
    name: "生图资源",
    resource_type: "image_generation",
    match_rules: { exact: [], prefix: [] },
    default_policy: {
      period_type: "weekly",
      requests: null,
      images: { enforced: true, limit: 50 },
    },
    enabled: true,
  });

  const [, init] = fetchWithAuth.mock.calls[0]! as [string, RequestInit];
  const payload = JSON.parse(init.body as string);
  expect(payload.resource_type).toBe("image_generation");
  expect(payload.default_policy.images).toEqual({ enforced: true, limit: 50 });
});

test("user override and restore encode user and scope identifiers", async () => {
  const { overrideUserCurrentPeriod, restoreUserCurrentPeriod } =
    await import("@/core/admin/api");

  await overrideUserCurrentPeriod("user/1", "scope/1", {
    requests: { enforced: false, limit: null },
    reason: "temporary",
  });
  await restoreUserCurrentPeriod("user/1", "scope/1");

  const [overrideUrl, overrideInit] = fetchWithAuth.mock.calls[0]! as [
    string,
    RequestInit,
  ];
  expect(overrideUrl).toContain(
    "/users/user%2F1/scopes/scope%2F1/current-period",
  );
  expect(JSON.parse(overrideInit.body as string).requests.limit).toBeNull();
  const [restoreUrl, restoreInit] = fetchWithAuth.mock.calls[1]! as [
    string,
    RequestInit,
  ];
  expect(restoreUrl).toContain("/current-period/override");
  expect(restoreInit.method).toBe("DELETE");
});

test("quota history sends date range and cursor", async () => {
  const { loadUserQuotaPeriods } = await import("@/core/admin/api");

  await loadUserQuotaPeriods("user/1", "scope/1", {
    start: "2026-06-01",
    end: "2026-07-15",
    limit: 10,
    cursor: "2026-07-01T00:00:00+00:00",
  });

  const url = new URL(
    fetchWithAuth.mock.calls[0]![0] as string,
    "http://localhost:2026",
  );
  expect(url.pathname).toContain("/users/user%2F1/scopes/scope%2F1/periods");
  expect(url.searchParams.get("start")).toBe("2026-06-01");
  expect(url.searchParams.get("cursor")).toBe("2026-07-01T00:00:00+00:00");
});

test("trace run list sends all exact filters and pagination", async () => {
  const { loadTraceRuns } = await import("@/core/admin/api");

  await loadTraceRuns({
    user_id: "user-1",
    thread_id: "thread-1",
    run_id: "run-1",
    page: 2,
    page_size: 15,
  });

  const url = new URL(
    fetchWithAuth.mock.calls[0]![0] as string,
    "http://localhost:2026",
  );
  expect(url.pathname).toBe("/api/admin/session-traces/runs");
  expect(url.searchParams.get("user_id")).toBe("user-1");
  expect(url.searchParams.get("thread_id")).toBe("thread-1");
  expect(url.searchParams.get("run_id")).toBe("run-1");
  expect(url.searchParams.get("page_size")).toBe("15");
});

test("trace overview and events encode path identifiers", async () => {
  const { loadTraceEvents, loadTraceUserOverview } =
    await import("@/core/admin/api");

  await loadTraceUserOverview("user/1");
  await loadTraceEvents("run/1", "thread/1");

  expect(fetchWithAuth.mock.calls[0]![0]).toContain("/users/user%2F1/overview");
  const eventUrl = new URL(
    fetchWithAuth.mock.calls[1]![0] as string,
    "http://localhost:2026",
  );
  expect(eventUrl.pathname).toContain("/runs/run%2F1/events");
  expect(eventUrl.searchParams.get("thread_id")).toBe("thread/1");
  expect(eventUrl.searchParams.get("limit")).toBe("500");
});
