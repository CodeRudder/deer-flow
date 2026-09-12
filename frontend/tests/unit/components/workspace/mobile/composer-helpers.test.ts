import { describe, expect, test } from "@rstest/core";

import { leadingSlashQuery } from "@/components/workspace/mobile/composer";
import { resolveMode } from "@/components/workspace/mobile/composer-mode";

/**
 * The pure parts of the composer.
 *
 * The mode resolution has a one-way coercion and the slash parser has to
 * reject `a/b` and `a b` alike; both are exported so this file can pin them.
 * The sticky-scroll hook's own helpers live next to it
 * (`use-scroll-to-bottom.test.ts`).
 */

describe("resolveMode", () => {
  test("keeps Flash for a model that cannot think", () => {
    expect(resolveMode("flash", false)).toBe("flash");
  });

  test("coerces every other mode to Flash for a model that cannot think", () => {
    // Mirrors the desktop's `getResolvedMode`: the request builder drops the
    // mode it cannot honour, so the UI must not disagree with the request.
    expect(resolveMode("thinking", false)).toBe("flash");
    expect(resolveMode("pro", false)).toBe("flash");
    expect(resolveMode("ultra", false)).toBe("flash");
  });

  test("defaults to Pro when the model can think and no mode was chosen", () => {
    expect(resolveMode(undefined, true)).toBe("pro");
  });

  test("defaults to Flash when the model cannot think and no mode was chosen", () => {
    expect(resolveMode(undefined, false)).toBe("flash");
  });

  test("passes an explicit mode through when the model supports thinking", () => {
    expect(resolveMode("flash", true)).toBe("flash");
    expect(resolveMode("thinking", true)).toBe("thinking");
    expect(resolveMode("ultra", true)).toBe("ultra");
  });
});

describe("leadingSlashQuery", () => {
  test("returns the query for a bare slash token", () => {
    expect(leadingSlashQuery("/web-search")).toBe("web-search");
  });

  test("preserves case — the caller lowercases for matching", () => {
    expect(leadingSlashQuery("/Web")).toBe("Web");
  });

  test("returns null when the text is not a slash token", () => {
    expect(leadingSlashQuery("")).toBeNull();
    expect(leadingSlashQuery("hello")).toBeNull();
    expect(leadingSlashQuery("hello /world")).toBeNull();
  });

  test("returns null once a second slash or whitespace closes the token", () => {
    // Otherwise the suggestion list would stay open while typing a path or a
    // sentence, and the first Enter would swap the text for a skill name.
    expect(leadingSlashQuery("/a/b")).toBeNull();
    expect(leadingSlashQuery("/skill name")).toBeNull();
  });

  test("treats a lone slash as an empty query", () => {
    // Stable and cheap: the caller reads it as "show everything".
    expect(leadingSlashQuery("/")).toBe("");
  });
});
