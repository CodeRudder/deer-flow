import { describe, expect, test } from "@rstest/core";

import {
  artifactDisplayName,
  artifactHref,
  artifactViewMode,
  decodeArtifactParam,
  isDownloadableArtifact,
  resolvedArtifactPath,
  writeFilePath,
} from "@/components/workspace/mobile/artifact-navigation";

/**
 * The artifact screen's two fiddly parts are both pure: the URL round trip
 * (an artifact identifier is a path, and sometimes a whole `write-file:` URL,
 * squeezed into one path segment) and the renderer dispatch. Neither needs a
 * browser, and both have branch boundaries worth pinning.
 */

const THREAD_ID = "00000000-0000-0000-0000-000000000001";

/** The synthetic identifier a `write_file` step in the transcript links to. */
const WRITE_FILE_URL =
  "write-file:/artifact-fixtures/report.html?message_id=msg-ai-1&tool_call_id=call-1";

describe("artifactHref", () => {
  test("keeps the whole identifier in one, encoded segment", () => {
    // Slashes and `?` must not survive: the first would add route segments,
    // the second would turn the identifier into a query string.
    expect(artifactHref(THREAD_ID, "/artifact-fixtures/report.html")).toBe(
      `/workspace/chats/${THREAD_ID}/artifacts/%2Fartifact-fixtures%2Freport.html`,
    );
  });

  test("is a public path — the internal /m/ prefix never appears", () => {
    // Plan §1.2.1: a phone is rewritten onto `/m/*` server-side, so a link that
    // spelled the prefix itself would leak it into the address bar.
    expect(artifactHref(THREAD_ID, "/a/b.md").startsWith("/workspace/")).toBe(
      true,
    );
    expect(artifactHref(THREAD_ID, "/a/b.md")).not.toContain("/m/");
  });

  test("carries the mock flag so the artifact screen keeps mocking", () => {
    expect(artifactHref(THREAD_ID, "/a/b.md", { isMock: true })).toBe(
      `/workspace/chats/${THREAD_ID}/artifacts/%2Fa%2Fb.md?mock=true`,
    );
  });

  test("round-trips a write-file identifier, query string included", () => {
    const href = artifactHref(THREAD_ID, WRITE_FILE_URL);
    const segment = href.split("/artifacts/")[1]!;
    expect(decodeArtifactParam(segment)).toBe(WRITE_FILE_URL);
  });
});

describe("decodeArtifactParam", () => {
  test("decodes an encoded segment", () => {
    expect(decodeArtifactParam("%2Fmnt%2Fuser-data%2Fout%2Freport.html")).toBe(
      "/mnt/user-data/out/report.html",
    );
  });

  test("leaves an already-decoded value alone", () => {
    // Whether Next.js hands over the raw or the decoded param is a router
    // detail; the helper tolerates both.
    expect(decodeArtifactParam("/mnt/user-data/out/report.html")).toBe(
      "/mnt/user-data/out/report.html",
    );
  });

  test("returns a literal `%` value instead of throwing", () => {
    expect(decodeArtifactParam("/out/100%25.html")).toBe("/out/100%.html");
    expect(decodeArtifactParam("/out/100%.html")).toBe("/out/100%.html");
  });
});

describe("writeFilePath / resolvedArtifactPath", () => {
  test("extracts the target path of a write-file identifier", () => {
    expect(writeFilePath(WRITE_FILE_URL)).toBe(
      "/artifact-fixtures/report.html",
    );
    expect(resolvedArtifactPath(WRITE_FILE_URL)).toBe(
      "/artifact-fixtures/report.html",
    );
  });

  test("passes a real path through untouched", () => {
    expect(writeFilePath("/mnt/out/report.html")).toBeUndefined();
    expect(resolvedArtifactPath("/mnt/out/report.html")).toBe(
      "/mnt/out/report.html",
    );
  });
});

describe("artifactDisplayName", () => {
  test("names the file, not the route segment", () => {
    expect(artifactDisplayName("/mnt/user-data/out/report.html")).toBe(
      "report.html",
    );
  });

  test("names the target of a write-file identifier", () => {
    expect(artifactDisplayName(WRITE_FILE_URL)).toBe("report.html");
  });
});

describe("isDownloadableArtifact", () => {
  test("a transcript draft has no URL to download from", () => {
    expect(isDownloadableArtifact(WRITE_FILE_URL)).toBe(false);
    expect(isDownloadableArtifact("/mnt/out/report.html")).toBe(true);
  });
});

describe("artifactViewMode", () => {
  test("an image is an image, even when its extension is also markup", () => {
    expect(artifactViewMode("/out/chart.png")).toBe("image");
    expect(artifactViewMode("/out/logo.svg")).toBe("image");
  });

  test("html and markdown get their rendered form", () => {
    expect(artifactViewMode("/out/report.html")).toBe("html");
    expect(artifactViewMode("/out/report.md")).toBe("markdown");
  });

  test("other source files get the code view", () => {
    expect(artifactViewMode("/out/data.json")).toBe("code");
    expect(artifactViewMode("/out/run.py")).toBe("code");
    expect(artifactViewMode("/out/notes.txt")).toBe("code");
  });

  test("what the browser can display itself gets an iframe", () => {
    expect(artifactViewMode("/out/paper.pdf")).toBe("iframe");
  });

  test("everything else is download-only", () => {
    expect(artifactViewMode("/out/archive.zip")).toBe("download");
    expect(artifactViewMode("/out/slides.pptx")).toBe("download");
  });

  test("the dispatch runs on the write-file target, not on the wrapper", () => {
    // Without unwrapping, `write-file:…` has no extension at all and every
    // draft would fall through to download-only.
    expect(
      artifactViewMode(
        "write-file:/out/report.html?message_id=m&tool_call_id=c",
      ),
    ).toBe("html");
  });
});
