import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { createElement } from "react";

import {
  isTabActive,
  MobileTabBar,
  normalizeMobilePathname,
  shouldHideMobileTabBar,
} from "@/components/workspace/mobile/tab-bar";

// The render assertions below need a pathname and translations; the pure
// functions above ignore both.
const mockPathname = rs.hoisted(() => ({ current: "/m/workspace" }));

const THREAD_ID = "00000000-0000-0000-0000-000000000001";
/** An artifact identifier is one percent-encoded segment (`artifactHref`). */
const ENCODED_ARTIFACT = "%2Fmnt%2Fuser-data%2Foutputs%2Freport.html";

rs.mock("next/navigation", () => ({
  usePathname: () => mockPathname.current,
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      sidebar: { chats: "Chats", agents: "Agents" },
      settings: { title: "Settings" },
    },
  }),
}));

afterEach(() => {
  cleanup();
  mockPathname.current = "/m/workspace";
});

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
  // The bar links to public paths (see TABS), so these are the real call
  // shapes: a post-rewrite `usePathname()` on one side, a public href on the
  // other.
  test("lights the tab whose root screen is showing", () => {
    expect(isTabActive("/m/workspace", "/workspace")).toBe(true);
    expect(isTabActive("/m/agents", "/agents")).toBe(true);
    expect(isTabActive("/m/settings", "/settings")).toBe(true);
  });

  test("stays lit on descendants", () => {
    expect(isTabActive("/m/workspace/chats/abc", "/workspace")).toBe(true);
    expect(isTabActive("/workspace/chats/abc", "/workspace")).toBe(true);
  });

  test("does not light a sibling tab, nor a path that merely shares the prefix", () => {
    expect(isTabActive("/m/settings", "/workspace")).toBe(false);
    expect(isTabActive("/m/agentsx", "/agents")).toBe(false);
    expect(isTabActive("/m/loginfo", "/login")).toBe(false);
  });

  test("works on the pre-rewrite path a rewritten entry reports", () => {
    expect(isTabActive("/workspace/chats/abc", "/workspace")).toBe(true);
    expect(isTabActive("/settings", "/settings")).toBe(true);
    expect(isTabActive("/workspace/chats/abc", "/agents")).toBe(false);
  });

  test("still accepts an /m/ href, in case one is ever linked directly", () => {
    expect(isTabActive("/m/workspace", "/m/workspace")).toBe(true);
    expect(isTabActive("/m/workspace/chats/abc", "/m/workspace")).toBe(true);
  });
});

describe("shouldHideMobileTabBar", () => {
  test("hides the bar on the signed-out screens", () => {
    expect(shouldHideMobileTabBar("/m/login")).toBe(true);
    expect(shouldHideMobileTabBar("/m/setup")).toBe(true);
    expect(shouldHideMobileTabBar("/m/auth/callback")).toBe(true);
  });

  test("hides it on the pre-rewrite paths the auth screens report", () => {
    // A phone reaching /login has its URL rewritten, but usePathname() still
    // reports the public path.
    expect(shouldHideMobileTabBar("/login")).toBe(true);
    expect(shouldHideMobileTabBar("/setup")).toBe(true);
    expect(shouldHideMobileTabBar("/auth/callback")).toBe(true);
  });

  test("keeps the bar on workspace screens", () => {
    expect(shouldHideMobileTabBar("/m/workspace")).toBe(false);
    expect(shouldHideMobileTabBar("/workspace/chats/abc")).toBe(false);
    expect(shouldHideMobileTabBar("/m/agents")).toBe(false);
    expect(shouldHideMobileTabBar("/m/settings/profile")).toBe(false);
  });

  test("does not hide on a path that merely shares a prefix", () => {
    expect(shouldHideMobileTabBar("/m/loginfo")).toBe(false);
    expect(shouldHideMobileTabBar("/m/setup-wizard")).toBe(false);
  });

  test("hides it on the full-screen artifact view (T6)", () => {
    // The artifact screen owns the bottom edge: it has an action bar and
    // carries the safe-area inset, so a tab bar underneath would stack two
    // bars (prototype ⑤ has none).
    expect(
      shouldHideMobileTabBar(
        `/m/workspace/chats/${THREAD_ID}/artifacts/${ENCODED_ARTIFACT}`,
      ),
    ).toBe(true);
    // The same screen on the pre-rewrite path `usePathname()` reports.
    expect(
      shouldHideMobileTabBar(
        `/workspace/chats/${THREAD_ID}/artifacts/${ENCODED_ARTIFACT}`,
      ),
    ).toBe(true);
  });

  test("still keeps the bar on the chat screen and the thread list", () => {
    // The pattern matches the artifact segment only — a `startsWith` on the
    // chat path would take the chat screen's bar away with it.
    expect(shouldHideMobileTabBar(`/m/workspace/chats/${THREAD_ID}`)).toBe(
      false,
    );
    expect(shouldHideMobileTabBar("/m/workspace/chats/new")).toBe(false);
    expect(shouldHideMobileTabBar("/m/workspace")).toBe(false);
  });
});

/**
 * The guard must not change what the bar renders anywhere else. When
 * `shouldHideMobileTabBar` is false the component falls through to the exact
 * branch it had before the guard was added.
 */
describe("MobileTabBar rendering", () => {
  test("still renders all three tabs on a workspace screen", () => {
    mockPathname.current = "/m/workspace";
    render(createElement(MobileTabBar));

    expect(screen.getAllByRole("link")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Chats" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Agents" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Settings" })).toBeTruthy();
    // The active tab keeps its marker.
    expect(
      screen.getByRole("link", { name: "Chats" }).getAttribute("aria-current"),
    ).toBe("page");
  });

  test("links to public paths so /m/ never reaches the address bar", () => {
    mockPathname.current = "/m/workspace";
    render(createElement(MobileTabBar));

    // A `<Link>` push of an `/m/` path skips the middleware matcher (it
    // excludes that prefix), so the internal path would land in the URL bar.
    const hrefs = screen
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(hrefs).toEqual(["/workspace", "/agents", "/settings"]);
    for (const href of hrefs) {
      expect(href?.startsWith("/m/")).toBe(false);
    }
  });

  test("renders nothing on the signed-out screens", () => {
    for (const pathname of ["/login", "/setup", "/auth/callback"]) {
      mockPathname.current = pathname;
      const { container } = render(createElement(MobileTabBar));
      expect(container.querySelector("nav")).toBeNull();
      cleanup();
    }
  });
});
