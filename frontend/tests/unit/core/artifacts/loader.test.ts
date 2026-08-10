import { afterEach, beforeEach, describe, expect, test, rs } from "@rstest/core";

async function loadFreshLoader() {
  rs.resetModules();
  return await import("@/core/artifacts/loader");
}

describe("loadArtifactContent", () => {
  const originalFetch = global.fetch;
  let fetchMock: ReturnType<typeof rs.fn>;

  beforeEach(() => {
    fetchMock = rs.fn(async () => ({ text: async () => "ARTIFACT-BODY" }));
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  test("returns artifact content and the resolved url", async () => {
    const { loadArtifactContent } = await loadFreshLoader();

    const result = await loadArtifactContent({
      filepath: "/mnt/user-data/outputs/repro.md",
      threadId: "thread-1",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.content).toBe("ARTIFACT-BODY");
    expect(result.url).toContain("/outputs/repro.md");
  });

  test("resolves the SKILL.md entry for .skill archives", async () => {
    const { loadArtifactContent } = await loadFreshLoader();

    await loadArtifactContent({
      filepath: "/mnt/user-data/outputs/my.skill",
      threadId: "thread-1",
    });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/my.skill/SKILL.md");
  });
});
