import {
  afterEach,
  beforeEach,
  describe,
  expect,
  test,
  rs,
} from "@rstest/core";

const ENV_KEYS = [
  "NEXT_PUBLIC_BACKEND_BASE_URL",
  "NEXT_PUBLIC_STATIC_WEBSITE_ONLY",
] as const;

type EnvSnapshot = Partial<
  Record<(typeof ENV_KEYS)[number], string | undefined>
>;

function snapshotEnv(): EnvSnapshot {
  const snapshot: EnvSnapshot = {};
  for (const key of ENV_KEYS) {
    snapshot[key] = process.env[key];
  }
  return snapshot;
}

function setEnv(key: (typeof ENV_KEYS)[number], value: string | undefined) {
  const env = process.env as Record<string, string | undefined>;
  if (value === undefined) {
    delete env[key];
  } else {
    env[key] = value;
  }
}

function restoreEnv(snapshot: EnvSnapshot) {
  for (const key of ENV_KEYS) {
    setEnv(key, snapshot[key]);
  }
}

async function loadFreshArtifactUtils() {
  rs.resetModules();
  return await import("@/core/artifacts/utils");
}

describe("artifact URL helpers", () => {
  let saved: EnvSnapshot;

  beforeEach(() => {
    saved = snapshotEnv();
    setEnv("NEXT_PUBLIC_BACKEND_BASE_URL", undefined);
    setEnv("NEXT_PUBLIC_STATIC_WEBSITE_ONLY", undefined);
  });

  afterEach(() => {
    restoreEnv(saved);
  });

  test("maps static demo artifact paths to bundled public files", async () => {
    setEnv("NEXT_PUBLIC_STATIC_WEBSITE_ONLY", "true");

    const { resolveArtifactURL, urlOfArtifact } =
      await loadFreshArtifactUtils();

    expect(
      urlOfArtifact({
        filepath: "/mnt/user-data/outputs/index.html",
        threadId: "thread-1",
      }),
    ).toBe("/demo/threads/thread-1/user-data/outputs/index.html");
    expect(
      resolveArtifactURL("/mnt/user-data/outputs/style.css", "thread-1"),
    ).toBe("/demo/threads/thread-1/user-data/outputs/style.css");
  });

  test("preview variant helpers", async () => {
    const {
      artifactImageSrc,
      isArtifactImageURL,
      withArtifactPreviewImage,
    } = await loadFreshArtifactUtils();

    expect(withArtifactPreviewImage("/api/threads/t1/artifacts/mnt/x.png")).toBe(
      "/api/threads/t1/artifacts/mnt/x.png?w=720&fmt=jpg",
    );
    expect(withArtifactPreviewImage("/api/threads/t1/artifacts/mnt/x.png?download=true")).toBe(
      "/api/threads/t1/artifacts/mnt/x.png?download=true&w=720&fmt=jpg",
    );
    expect(withArtifactPreviewImage("")).toBe("");

    expect(isArtifactImageURL("/api/threads/t1/artifacts/mnt/x.png")).toBe(true);
    expect(isArtifactImageURL("/mock/api/threads/t1/artifacts/mnt/x.png")).toBe(true);
    expect(isArtifactImageURL("https://example.com/pic.png")).toBe(false);
    expect(isArtifactImageURL("/api/threads/t1/uploads/x.png")).toBe(false);

    // Artifact image URLs get the variant; everything else is untouched.
    expect(artifactImageSrc("/api/threads/t1/artifacts/mnt/x.png")).toBe(
      "/api/threads/t1/artifacts/mnt/x.png?w=720&fmt=jpg",
    );
    expect(artifactImageSrc("https://example.com/pic.png")).toBe(
      "https://example.com/pic.png",
    );
    expect(artifactImageSrc("/api/threads/t1/uploads/x.png")).toBe(
      "/api/threads/t1/uploads/x.png",
    );
  });
});
