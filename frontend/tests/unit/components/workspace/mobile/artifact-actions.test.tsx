import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  canShareFiles,
  MobileArtifactActions,
} from "@/components/workspace/mobile/artifact-actions";
import { I18nProvider } from "@/core/i18n/context";

/**
 * A5: 分享 prefers the OS share sheet and falls back to the download the bar
 * already offers (T21/T26).
 *
 * Two layers are pinned here. `canShareFiles` is the decision itself — the
 * environment it reads is a parameter, so both answers are checked without a
 * browser. The component tests then prove the wiring end to end: an accepted
 * file reaches `navigator.share`, and every refusal — no share API, a refused
 * payload, a failed fetch — ends at the download, so the tap is never a no-op.
 */

const DOWNLOAD_URL =
  "/api/threads/thread-1/artifacts/artifact-fixtures/data.json?download=true";
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

function renderBar(
  props: { downloadUrl?: string; onRefresh?: () => void } = {},
) {
  return render(
    <I18nProvider initialLocale="zh-CN">
      <MobileArtifactActions
        downloadUrl={props.downloadUrl}
        fileName={props.downloadUrl ? FILE_NAME : undefined}
        onRefresh={props.onRefresh}
      />
    </I18nProvider>,
  );
}

describe("MobileArtifactActions 分享", () => {
  it("hands the file to the share sheet when the platform takes files", async () => {
    const user = userEvent.setup();
    const clicked = captureProgrammaticClicks();
    const shareCalls = installShareApi(true);
    stubArtifactFetch();
    renderBar({ downloadUrl: DOWNLOAD_URL });

    await user.click(screen.getByTestId("mobile-artifact-share"));

    expect(shareCalls).toHaveLength(1);
    expect(shareCalls[0]?.title).toBe(FILE_NAME);
    expect(shareCalls[0]?.files?.[0]?.name).toBe(FILE_NAME);
    // Sharing succeeded, so the download was not also triggered.
    expect(clicked).toEqual([]);
  });

  it("falls back to the download when the platform refuses files", async () => {
    const user = userEvent.setup();
    const clicked = captureProgrammaticClicks();
    const shareCalls = installShareApi(false);
    stubArtifactFetch();
    renderBar({ downloadUrl: DOWNLOAD_URL });

    await user.click(screen.getByTestId("mobile-artifact-share"));

    expect(shareCalls).toEqual([]);
    expect(clicked).toEqual([DOWNLOAD_URL]);
  });

  it("falls back to the download when the bytes cannot be fetched", async () => {
    const user = userEvent.setup();
    const clicked = captureProgrammaticClicks();
    const shareCalls = installShareApi(true);
    stubArtifactFetch(false);
    renderBar({ downloadUrl: DOWNLOAD_URL });

    await user.click(screen.getByTestId("mobile-artifact-share"));

    expect(shareCalls).toEqual([]);
    expect(clicked).toEqual([DOWNLOAD_URL]);
  });

  it("offers no share for an artifact with no URL to fetch", () => {
    // A `write-file:` draft exists only in the transcript: there are no bytes
    // to hand to a share sheet, and nothing to download either.
    renderBar();

    expect(screen.queryByTestId("mobile-artifact-share")).toBeNull();
    expect(screen.queryByTestId("mobile-artifact-download")).toBeNull();
  });
});

describe("MobileArtifactActions 刷新", () => {
  it("re-fetches through the callback the screen owns", async () => {
    const user = userEvent.setup();
    let refreshed = 0;
    renderBar({
      downloadUrl: DOWNLOAD_URL,
      onRefresh: () => {
        refreshed += 1;
      },
    });

    await user.click(screen.getByTestId("mobile-artifact-refresh"));

    expect(refreshed).toBe(1);
  });

  it("stays away when there is nothing to re-fetch", () => {
    renderBar({ downloadUrl: DOWNLOAD_URL });

    expect(screen.queryByTestId("mobile-artifact-refresh")).toBeNull();
  });
});
