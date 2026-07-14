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

test("quota list sends custom month, filter, search, and pagination", async () => {
  const { loadQuotaUsers } = await import("@/core/admin/api");

  await loadQuotaUsers({
    period: "custom",
    period_start: "2026-05-01",
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
  expect(url.searchParams.get("period_start")).toBe("2026-05-01");
  expect(url.searchParams.get("status")).toBe("exceeded");
  expect(url.searchParams.get("keyword")).toBe("user@example.com");
  expect(url.searchParams.get("page")).toBe("3");
  expect(url.searchParams.get("page_size")).toBe("50");
});

test("quota update keeps null limit in JSON payload", async () => {
  const { updateQuotaUser } = await import("@/core/admin/api");

  await updateQuotaUser("user/1", {
    period: "this_month",
    image_generations: { enabled: false, limit: null },
  });

  const [url, init] = fetchWithAuth.mock.calls[0]! as [string, RequestInit];
  expect(url).toContain("/api/admin/quotas/users/user%2F1");
  expect(init.method).toBe("PUT");
  expect(typeof init.body).toBe("string");
  expect(JSON.parse(init.body as string)).toEqual({
    period: "this_month",
    image_generations: { enabled: false, limit: null },
  });
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
