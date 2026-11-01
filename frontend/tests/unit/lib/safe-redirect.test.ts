import { describe, expect, test } from "@rstest/core";

import { isSafeRelativePath, resolveSafeRedirect } from "@/lib/safe-redirect";

describe("isSafeRelativePath", () => {
  test("accepts same-origin relative paths", () => {
    expect(isSafeRelativePath("/workspace")).toBe(true);
    expect(isSafeRelativePath("/workspace/chats/abc")).toBe(true);
    expect(isSafeRelativePath("/workspace?tab=1")).toBe(true);
  });

  test("rejects absolute and protocol-relative URLs", () => {
    expect(isSafeRelativePath("https://evil.example")).toBe(false);
    expect(isSafeRelativePath("http://evil.example/login")).toBe(false);
    expect(isSafeRelativePath("//evil.example")).toBe(false);
  });

  test("rejects non-http schemes and bare relative paths", () => {
    expect(isSafeRelativePath("javascript:alert(1)")).toBe(false);
    expect(isSafeRelativePath("data:text/html,<script>")).toBe(false);
    // A colon anywhere is enough — the desktop validator only checks the prefix.
    expect(isSafeRelativePath("/workspace:a")).toBe(false);
    expect(isSafeRelativePath("../../etc/passwd")).toBe(false);
  });

  test("rejects empty and missing values", () => {
    expect(isSafeRelativePath("")).toBe(false);
    expect(isSafeRelativePath(null)).toBe(false);
    expect(isSafeRelativePath(undefined)).toBe(false);
  });
});

describe("resolveSafeRedirect", () => {
  test("returns the validated path when safe", () => {
    expect(resolveSafeRedirect("/workspace/chats/abc")).toBe(
      "/workspace/chats/abc",
    );
  });

  test("falls back to the workspace for unsafe or missing values", () => {
    expect(resolveSafeRedirect("https://evil.example")).toBe("/workspace");
    expect(resolveSafeRedirect(null)).toBe("/workspace");
    expect(resolveSafeRedirect(undefined)).toBe("/workspace");
  });

  test("honours an explicit fallback", () => {
    expect(resolveSafeRedirect(null, "/login")).toBe("/login");
  });
});
