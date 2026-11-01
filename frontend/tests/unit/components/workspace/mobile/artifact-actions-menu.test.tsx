import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";

import {
  canShareFiles,
  MobileArtifactActionsMenu,
} from "@/components/workspace/mobile/artifact-actions-menu";
import { I18nProvider } from "@/core/i18n/context";

/**
 * The artifact screen's tools, from the ⋯ to the action (prototype ⑤b).
 *
 * Two layers are pinned here. `canShareFiles` is the share decision itself —
 * the environment it reads is a parameter, so both answers are checked without
 * a browser. The component tests then prove the wiring end to end: the ⋯ opens
 * the menu, each tool is listed only when it applies, picking one runs it and
 * closes the menu, and every 分享 refusal — no share API, a refused payload, a
 * failed fetch — ends at the download, so the tap is never a no-op.
 */

/**
 * cmdk, which backs the dialog's list, uses two DOM APIs jsdom does not
 * implement: a `ResizeObserver` to measure the list, and `scrollIntoView` to
 * bring the selected row into view. Neither is what this file asserts — the
 * 44px row floor is measured in a real browser, in `mobile-artifacts.spec.ts`
 * — so no-op stand-ins are all the dialog needs to render.
 */
if (typeof globalThis.ResizeObserver === "undefined") {
  const noop = () => undefined;
  globalThis.ResizeObserver = class {
    observe = noop;
    unobserve = noop;
    disconnect = noop;
  } as unknown as typeof ResizeObserver;
}

if (typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = () => undefined;
}

/**
 * `useI18n` re-reads the locale from the cookie on mount — it overrides the
 * `I18nProvider`'s initial value — and jsdom has no stored preference, so the
 * two tests that read a label ("自动换行" / "不换行") pin the locale here rather
 * than depend on whatever `navigator.language` the runner reports.
 */
document.cookie = "locale=zh-CN; path=/";

const DOWNLOAD_URL =
  "/api/threads/thread-1/artifacts/artifact-fixtures/data.json?download=true";
const OPEN_URL = "/api/threads/thread-1/artifacts/artifact-fixtures/data.json";
const FILE_NAME = "data.json";
const ARTIFACT_BODY = '{"status":"draft"}';

type ShareNavigator = {
  share?: (data?: ShareData) => Promise<void>;
  canShare?: (data?: ShareData) => boolean;
};

function artifactFile(): File {
  return new File([ARTIFACT_BODY], FILE_NAME, { type: "application/json" });
}

// ---------------------------------------------------------------------------
// The decision
// ---------------------------------------------------------------------------

describe("canShareFiles", () => {
  it("asks the platform about the actual file, and says yes when it agrees", () => {
    const asked: ShareData[] = [];
    const nav: ShareNavigator = {
      share: () => Promise.resolve(),
      canShare: (data) => {
        asked.push(data ?? {});
        return true;
      },
    };

    expect(canShareFiles(nav, artifactFile())).toBe(true);
    // The check has to carry the file: `canShare({})` would answer a different
    // question than the one the share sheet will ask.
    expect(asked[0]?.files?.[0]?.name).toBe(FILE_NAME);
  });

  it("says no when the platform refuses a file payload", () => {
    const nav: ShareNavigator = {
      share: () => Promise.resolve(),
      canShare: () => false,
    };

    expect(canShareFiles(nav, artifactFile())).toBe(false);
  });

  it("says no where there is no share API at all", () => {
    // Desktop Firefox, and any non-secure origin.
    expect(canShareFiles({ canShare: () => true }, artifactFile())).toBe(false);
    expect(canShareFiles(undefined, artifactFile())).toBe(false);
  });

  it("says no when the platform cannot answer before the tap", () => {
    // `share` without `canShare`: nothing can be promised in advance.
    expect(
      canShareFiles({ share: () => Promise.resolve() }, artifactFile()),
    ).toBe(false);
  });

  it("treats a throwing canShare as a no rather than an exception", () => {
    const nav: ShareNavigator = {
      share: () => Promise.resolve(),
      canShare: () => {
        throw new TypeError("payload not inspectable");
      },
    };

    expect(canShareFiles(nav, artifactFile())).toBe(false);
  });

  it("says no without a file", () => {
    const nav: ShareNavigator = {
      share: () => Promise.resolve(),
      canShare: () => true,
    };

    expect(canShareFiles(nav, null)).toBe(false);
    expect(canShareFiles(nav, undefined)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// The wiring
// ---------------------------------------------------------------------------

/** Records the hrefs the component opens by itself (the share fallback). */
function captureProgrammaticClicks(): string[] {
  const clicked: string[] = [];
  const previous = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "click",
  );
  Object.defineProperty(HTMLElement.prototype, "click", {
    configurable: true,
    writable: true,
    value(this: HTMLElement) {
      const href = this.getAttribute("href");
      if (href) {
        clicked.push(href);
      }
    },
  });
  restoreClick = () => {
    if (previous) {
      Object.defineProperty(HTMLElement.prototype, "click", previous);
    } else {
      Reflect.deleteProperty(HTMLElement.prototype, "click");
    }
  };
  return clicked;
}

/** Installs a share API that answers `accepts` to `canShare`. */
function installShareApi(accepts: boolean): ShareData[] {
  const target = navigator as unknown as ShareNavigator;
  const previousShare = Object.getOwnPropertyDescriptor(target, "share");
  const previousCanShare = Object.getOwnPropertyDescriptor(target, "canShare");
  const calls: ShareData[] = [];

  Object.defineProperty(target, "share", {
    configurable: true,
    value: (data?: ShareData) => {
      calls.push(data ?? {});
      return Promise.resolve();
    },
  });
  Object.defineProperty(target, "canShare", {
    configurable: true,
    value: () => accepts,
  });

  restoreShare = () => {
    if (previousShare) {
      Object.defineProperty(target, "share", previousShare);
    } else {
      Reflect.deleteProperty(target, "share");
    }
    if (previousCanShare) {
      Object.defineProperty(target, "canShare", previousCanShare);
    } else {
      Reflect.deleteProperty(target, "canShare");
    }
  };
  return calls;
}

/** The artifact bytes endpoint, as the component's `fetch` sees it. */
function stubArtifactFetch(ok = true) {
  const previous = globalThis.fetch;
  globalThis.fetch = (() =>
    Promise.resolve({
      ok,
      blob: () =>
        Promise.resolve(
          new Blob([ARTIFACT_BODY], { type: "application/json" }),
        ),
    })) as unknown as typeof fetch;
  restoreFetch = () => {
    globalThis.fetch = previous;
  };
}

let restoreClick: (() => void) | undefined;
let restoreShare: (() => void) | undefined;
let restoreFetch: (() => void) | undefined;

afterEach(() => {
  cleanup();
  restoreClick?.();
  restoreShare?.();
  restoreFetch?.();
  restoreClick = undefined;
  restoreShare = undefined;
  restoreFetch = undefined;
});

type MenuProps = ComponentProps<typeof MobileArtifactActionsMenu>;

/** Every tool the screen can offer: the shape of a code file with a URL. */
const ALL_TOOLS: MenuProps = {
  downloadUrl: DOWNLOAD_URL,
  openUrl: OPEN_URL,
  fileName: FILE_NAME,
  wrap: false,
  onWrapChange: () => undefined,
  onCopy: () => undefined,
  onRefresh: () => undefined,
};

function renderMenu(props: MenuProps = {}) {
  return render(
    <I18nProvider initialLocale="zh-CN">
      <MobileArtifactActionsMenu {...props} />
    </I18nProvider>,
  );
}

/** Opens the menu the way the operator does: the title bar's ⋯. */
async function openMenu() {
  const user = userEvent.setup();
  await user.click(screen.getByTestId("mobile-artifact-tools"));
  return user;
}

/**
 * The rows' testids, in the order they render. A link row carries its testid
 * on the `<a>` (so the href is assertable), a callback row on the row itself.
 */
function rowTestids(): (string | undefined)[] {
  return screen
    .getAllByRole("option")
    .map(
      (row) =>
        row.getAttribute("data-testid") ??
        row.querySelector("[data-testid]")?.getAttribute("data-testid") ??
        undefined,
    );
}

describe("MobileArtifactActionsMenu", () => {
  it("opens the tools from the ⋯, in ⑤b's order", async () => {
    renderMenu(ALL_TOOLS);

    // Nothing is painted until the ⋯ is tapped — the tools live in a dialog.
    expect(screen.queryByTestId("mobile-artifact-share")).toBeNull();

    await openMenu();

    expect(rowTestids()).toEqual([
      "mobile-artifact-share",
      "mobile-artifact-download",
      "mobile-artifact-copy",
      "mobile-artifact-open",
      "mobile-artifact-wrap",
      "mobile-artifact-refresh",
    ]);
  });

  it("renders no ⋯ at all when no tool applies", () => {
    // A `write-file:` draft with no content to fetch: no share, no download,
    // no open, no copy, nothing to wrap and nothing to re-fetch. An empty menu
    // is not an option, so the entry point is not there either.
    renderMenu();

    expect(screen.queryByTestId("mobile-artifact-tools")).toBeNull();
    expect(screen.queryByRole("option")).toBeNull();
  });

  it("keeps a tool off the menu when it does not apply", async () => {
    // A URL but no wrapped/marked-up view: share and download only.
    renderMenu({ downloadUrl: DOWNLOAD_URL, fileName: FILE_NAME });

    await openMenu();

    expect(rowTestids()).toEqual([
      "mobile-artifact-share",
      "mobile-artifact-download",
    ]);
  });

  it("closes the menu once a tool is picked", async () => {
    let refreshed = 0;
    renderMenu({
      ...ALL_TOOLS,
      onRefresh: () => {
        refreshed += 1;
      },
    });

    const user = await openMenu();
    await user.click(screen.getByTestId("mobile-artifact-refresh"));

    expect(refreshed).toBe(1);
    // The result shows behind the menu, so the menu gets out of the way.
    expect(screen.queryByTestId("mobile-artifact-refresh")).toBeNull();
  });

  it("offers the wrap toggle as the opposite of the current state", async () => {
    const toggles: boolean[] = [];
    renderMenu({
      ...ALL_TOOLS,
      wrap: false,
      onWrapChange: (wrap) => toggles.push(wrap),
    });

    const user = await openMenu();
    await user.click(screen.getByText("自动换行"));

    expect(toggles).toEqual([true]);

    // Re-opened on a wrapped file, the row is the way back.
    cleanup();
    renderMenu({ ...ALL_TOOLS, wrap: true });
    await openMenu();
    expect(screen.getByText("不换行")).toBeTruthy();
  });

  it("copies only when there is content, and lists it as unavailable until then", async () => {
    let copies = 0;
    renderMenu({
      ...ALL_TOOLS,
      copyDisabled: true,
      onCopy: () => {
        copies += 1;
      },
    });

    const user = await openMenu();
    const copy = screen.getByTestId("mobile-artifact-copy");
    // cmdk marks a disabled row `aria-disabled` and drops its click handler.
    expect(copy.getAttribute("aria-disabled")).toBe("true");

    await user.click(copy);
    expect(copies).toBe(0);
  });

  it("offers no share for an artifact with no URL to fetch", async () => {
    // A `write-file:` draft exists only in the transcript: there are no bytes
    // to hand to a share sheet, and nothing to download either.
    renderMenu({ onRefresh: () => undefined });

    await openMenu();

    expect(rowTestids()).toEqual(["mobile-artifact-refresh"]);
  });
});

describe("MobileArtifactActionsMenu 分享", () => {
  it("hands the file to the share sheet when the platform takes files", async () => {
    const clicked = captureProgrammaticClicks();
    const shareCalls = installShareApi(true);
    stubArtifactFetch();
    renderMenu({ downloadUrl: DOWNLOAD_URL, fileName: FILE_NAME });

    const user = await openMenu();
    await user.click(screen.getByTestId("mobile-artifact-share"));

    expect(shareCalls).toHaveLength(1);
    expect(shareCalls[0]?.title).toBe(FILE_NAME);
    expect(shareCalls[0]?.files?.[0]?.name).toBe(FILE_NAME);
    // Sharing succeeded, so the download was not also triggered.
    expect(clicked).toEqual([]);
  });

  it("falls back to the download when the platform refuses files", async () => {
    const clicked = captureProgrammaticClicks();
    const shareCalls = installShareApi(false);
    stubArtifactFetch();
    renderMenu({ downloadUrl: DOWNLOAD_URL, fileName: FILE_NAME });

    const user = await openMenu();
    await user.click(screen.getByTestId("mobile-artifact-share"));

    expect(shareCalls).toEqual([]);
    expect(clicked).toEqual([DOWNLOAD_URL]);
  });

  it("falls back to the download when the bytes cannot be fetched", async () => {
    const clicked = captureProgrammaticClicks();
    const shareCalls = installShareApi(true);
    stubArtifactFetch(false);
    renderMenu({ downloadUrl: DOWNLOAD_URL, fileName: FILE_NAME });

    const user = await openMenu();
    await user.click(screen.getByTestId("mobile-artifact-share"));

    expect(shareCalls).toEqual([]);
    expect(clicked).toEqual([DOWNLOAD_URL]);
  });
});

describe("MobileArtifactActionsMenu 刷新", () => {
  it("re-fetches through the callback the screen owns", async () => {
    let refreshed = 0;
    renderMenu({
      downloadUrl: DOWNLOAD_URL,
      onRefresh: () => {
        refreshed += 1;
      },
    });

    const user = await openMenu();
    await user.click(screen.getByTestId("mobile-artifact-refresh"));

    expect(refreshed).toBe(1);
  });

  it("stays away when there is nothing to re-fetch", async () => {
    renderMenu({ downloadUrl: DOWNLOAD_URL });

    await openMenu();

    expect(rowTestids()).toEqual(["mobile-artifact-download"]);
    expect(screen.queryByTestId("mobile-artifact-refresh")).toBeNull();
  });
});
