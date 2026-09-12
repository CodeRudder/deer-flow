import { describe, expect, test } from "@rstest/core";

import {
  isTabActive,
  normalizeMobilePathname,
} from "@/components/workspace/mobile/tab-bar";

describe("normalizeMobilePathname", () => {
  test("leaves an already-mobile path alone", () => {
    expect(normalizeMobilePathname("/m/workspace")).toBe("/m/workspace");
    expect(normalizeMobilePathname("/m")).toBe("/m");
  });

  test("maps the pre-rewrite path usePathname reports into the mobile tree", () => {
    // Rewritten `/workspace/chats/abc` renders `/m/workspace/chats/[thread_id]`,
    // but `usePathname()` still reports the browser URL.
    expect(normalizeMobilePathname("/workspace/chats/abc")).toBe(
      "/m/workspace/chats/abc",
    );
  });
});

describe("isTabActive", () => {
  test("lights the tab whose root screen is showing", () => {
    expect(isTabActive("/m/workspace", "/m/workspace")).toBe(true);
    expect(isTabActive("/m/agents", "/m/agents")).toBe(true);
    expect(isTabActive("/m/settings", "/m/settings")).toBe(true);
  });

  test("stays lit on descendants", () => {
    expect(isTabActive("/m/workspace/chats/abc", "/m/workspace")).toBe(true);
  });

  test("does not light a sibling tab, nor a path that merely shares the prefix", () => {
    expect(isTabActive("/m/settings", "/m/workspace")).toBe(false);
    expect(isTabActive("/m/agentsx", "/m/agents")).toBe(false);
  });

  test("works on the pre-rewrite path a rewritten entry reports", () => {
    expect(isTabActive("/workspace/chats/abc", "/m/workspace")).toBe(true);
    expect(isTabActive("/settings", "/m/settings")).toBe(true);
    expect(isTabActive("/workspace/chats/abc", "/m/agents")).toBe(false);
  });
});
