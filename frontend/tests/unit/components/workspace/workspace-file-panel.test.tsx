import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  getAllByText,
  render,
  waitFor,
} from "@testing-library/react";

import { WorkspaceFilePanel } from "@/components/workspace/workspace-file-panel/workspace-file-panel";
import {
  useWorkspaceFilesPanelOpen,
  WorkspaceFilesToggle,
  WORKSPACE_FILES_PANEL_OPEN_KEY,
} from "@/components/workspace/workspace-file-panel/workspace-files-toggle";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { close: "Close", retry: "Retry", download: "Download" },
      workspaceFiles: {
        title: "Files",
        toggle: "Toggle workspace files panel",
        rootName: "workspace",
        up: "Up one level",
        refresh: "Refresh",
        searchPlaceholder: "Search files…",
        loading: "Loading…",
        empty: "No files in this directory.",
        noResults: "No matching files.",
        notFound: "This directory does not exist.",
        loadError: "Failed to load files.",
        truncated: "List truncated.",
        manualRefreshHint: "Files refresh manually.",
        tooLargeToPreview: "Too large to preview inline.",
      },
    },
  }),
}));

rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "http://gw" }));

const fetchMock = rs.hoisted(() => rs.fn());
rs.mock("@/core/api/fetcher", () => ({ fetch: fetchMock }));

// Preview dialog collaborators: the artifacts preview machinery is mocked at
// its seams (context hook + renderer) so the click-path routing is what is
// under test, not Streamdown / CodeMirror internals.
rs.mock("@/components/workspace/messages/context", () => ({
  useThread: () => ({ thread: {}, isMock: false }),
}));

const useArtifactContent = rs.hoisted(() => rs.fn());
rs.mock("@/core/artifacts/hooks", () => ({ useArtifactContent }));

rs.mock("@/components/workspace/artifacts", () => ({
  ArtifactFilePreview: (props: { content: string; language: string }) => (
    <div data-testid="artifact-file-preview" data-language={props.language}>
      {props.content}
    </div>
  ),
}));

rs.mock("@/components/workspace/code-editor", () => ({
  CodeEditor: (props: { value: string }) => (
    <div data-testid="code-editor">{props.value}</div>
  ),
}));

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: async () => body,
  };
}

const ROOT_LISTING = {
  path: "/mnt/user-data",
  truncated: false,
  entries: [
    {
      name: "outputs",
      path: "/mnt/user-data/outputs",
      type: "directory",
      size: null,
      modified_at: "2026-11-01T12:00:00",
    },
    {
      name: "report.pdf",
      path: "/mnt/user-data/report.pdf",
      type: "file",
      size: 2048,
      modified_at: "2026-11-01T12:00:00",
    },
    {
      name: "notes.md",
      path: "/mnt/user-data/notes.md",
      type: "file",
      size: 128,
      modified_at: "2026-11-01T12:00:00",
    },
    {
      name: "script.py",
      path: "/mnt/user-data/script.py",
      type: "file",
      size: 256,
      modified_at: "2026-11-01T12:00:00",
    },
    {
      name: "huge.csv",
      path: "/mnt/user-data/huge.csv",
      type: "file",
      size: 4 * 1024 * 1024,
      modified_at: "2026-11-01T12:00:00",
    },
    {
      name: "demo.mp4",
      path: "/mnt/user-data/demo.mp4",
      type: "file",
      size: 4096,
      modified_at: "2026-11-01T12:00:00",
    },
  ],
};

const OUTPUTS_LISTING = {
  path: "/mnt/user-data/outputs",
  truncated: false,
  entries: [
    {
      name: "chart.png",
      path: "/mnt/user-data/outputs/chart.png",
      type: "file",
      size: 1024,
      modified_at: "2026-11-02T09:00:00",
    },
  ],
};

function listing(url: string) {
  const decoded = decodeURIComponent(url);
  if (url.includes("search=")) {
    return jsonResponse({
      path: "/mnt/user-data",
      truncated: false,
      entries: [
        {
          name: "deep.txt",
          path: "/mnt/user-data/outputs/deep.txt",
          type: "file",
          size: 12,
          modified_at: "2026-11-02T09:00:00",
        },
      ],
    });
  }
  if (decoded.includes("/mnt/user-data/outputs")) {
    return jsonResponse(OUTPUTS_LISTING);
  }
  return jsonResponse(ROOT_LISTING);
}

function urlCalls() {
  return fetchMock.mock.calls.map((call) => String(call[0]));
}

beforeEach(() => {
  fetchMock.mockClear();
  fetchMock.mockImplementation((url: string) => Promise.resolve(listing(url)));
  useArtifactContent.mockReset().mockReturnValue({
    content: "# Hello workspace",
    url: "http://gw/api/threads/t1/artifacts/mnt/user-data/notes.md",
    isLoading: false,
    error: null,
  });
  window.localStorage.clear();
});

afterEach(cleanup);

// The panel renders the desktop column and the mobile drawer with the same
// data-testids (responsive CSS picks one at runtime) — query the first.
function byTestId(container: HTMLElement, id: string): HTMLElement {
  const element = container.querySelector(`[data-testid="${id}"]`);
  if (!element) throw new Error(`Missing [data-testid="${id}"]`);
  return element as HTMLElement;
}

function queryByTestId(container: HTMLElement, id: string) {
  return container.querySelector(`[data-testid="${id}"]`);
}

function ToggleHarness() {
  const [open, toggle] = useWorkspaceFilesPanelOpen();
  return (
    <div>
      <WorkspaceFilesToggle open={open} onToggle={toggle} />
      <WorkspaceFilePanel threadId="t1" open={open} onClose={toggle} />
    </div>
  );
}

describe("WorkspaceFilePanel", () => {
  it("renders directory entries from the API when opened", async () => {
    const { container } = render(
      <WorkspaceFilePanel threadId="t1" open={true} />,
    );
    await waitFor(() => {
      expect(queryByTestId(container, "workspace-files-list")).toBeTruthy();
    });
    const list = byTestId(container, "workspace-files-list");
    expect(list.textContent).toContain("outputs");
    expect(list.textContent).toContain("report.pdf");
    expect(list.textContent).toContain("2.0 KB");
    expect(urlCalls()[0]).toBe(
      "http://gw/api/threads/t1/workspace/files?path=%2Fmnt%2Fuser-data",
    );
  });

  it("renders nothing when closed", () => {
    const { container } = render(
      <WorkspaceFilePanel threadId="t1" open={false} />,
    );
    expect(queryByTestId(container, "workspace-files-panel")).toBeNull();
  });

  it("navigates into a directory on click and back via breadcrumbs/up", async () => {
    const { container } = render(
      <WorkspaceFilePanel threadId="t1" open={true} />,
    );
    await waitFor(() => {
      expect(byTestId(container, "workspace-files-list").textContent).toContain(
        "report.pdf",
      );
    });

    // Click the directory entry to navigate into it (first = desktop column).
    fireEvent.click(getAllByText(container, "outputs")[0]!);
    await waitFor(() => {
      expect(byTestId(container, "workspace-files-list").textContent).toContain(
        "chart.png",
      );
    });
    expect(
      urlCalls().some((url) =>
        url.includes(encodeURIComponent("/mnt/user-data/outputs")),
      ),
    ).toBe(true);

    // Breadcrumb root click returns to the virtual root.
    fireEvent.click(getAllByText(container, "workspace")[0]!);
    await waitFor(() => {
      expect(byTestId(container, "workspace-files-list").textContent).toContain(
        "report.pdf",
      );
    });

    // Up button navigates to the parent again.
    fireEvent.click(getAllByText(container, "outputs")[0]!);
    await waitFor(() => {
      expect(byTestId(container, "workspace-files-list").textContent).toContain(
        "chart.png",
      );
    });
    fireEvent.click(byTestId(container, "workspace-files-up"));
    await waitFor(() => {
      expect(byTestId(container, "workspace-files-list").textContent).toContain(
        "report.pdf",
      );
    });
  });

  it("debounces search input into a search API call and shows flat results", async () => {
    const { container } = render(
      <WorkspaceFilePanel threadId="t1" open={true} />,
    );
    await waitFor(() => {
      expect(queryByTestId(container, "workspace-files-list")).toBeTruthy();
    });
    const callsBefore = urlCalls().length;

    fireEvent.change(byTestId(container, "workspace-files-search"), {
      target: { value: "deep" },
    });

    // Not called before the debounce window elapses.
    expect(urlCalls().length).toBe(callsBefore);

    await waitFor(
      () => {
        expect(urlCalls().some((url) => url.includes("search=deep"))).toBe(
          true,
        );
      },
      { timeout: 2000 },
    );
    await waitFor(() => {
      expect(byTestId(container, "workspace-files-list").textContent).toContain(
        "deep.txt",
      );
    });
  }, 10000);

  it("shows the not-found state on 404 and error state with retry on failure", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("search=")) return listing(url);
      return Promise.resolve(jsonResponse({ detail: "nope" }, 404));
    });
    const { container } = render(
      <WorkspaceFilePanel threadId="t1" open={true} />,
    );
    await waitFor(() => {
      expect(
        queryByTestId(container, "workspace-files-not-found"),
      ).toBeTruthy();
    });

    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse({ detail: "boom" }, 500)),
    );
    cleanup();
    const view = render(<WorkspaceFilePanel threadId="t1" open={true} />);
    await waitFor(() => {
      expect(
        queryByTestId(view.container, "workspace-files-error"),
      ).toBeTruthy();
    });
    fireEvent.click(getAllByText(view.container, "Retry")[0]!);
    await waitFor(() => {
      expect(urlCalls().length).toBeGreaterThan(1);
    });
  });

  it("toggle persists to localStorage and shows/hides the panel", async () => {
    const { container } = render(<ToggleHarness />);
    // Initially closed (hydration-guarded default).
    expect(queryByTestId(container, "workspace-files-panel")).toBeNull();
    expect(window.localStorage.getItem(WORKSPACE_FILES_PANEL_OPEN_KEY)).toBe(
      null,
    );

    fireEvent.click(byTestId(container, "workspace-files-toggle"));
    await waitFor(() => {
      expect(queryByTestId(container, "workspace-files-panel")).toBeTruthy();
    });
    expect(window.localStorage.getItem(WORKSPACE_FILES_PANEL_OPEN_KEY)).toBe(
      "1",
    );

    fireEvent.click(byTestId(container, "workspace-files-toggle"));
    await waitFor(() => {
      expect(queryByTestId(container, "workspace-files-panel")).toBeNull();
    });
    expect(window.localStorage.getItem(WORKSPACE_FILES_PANEL_OPEN_KEY)).toBe(
      "0",
    );
  });

  it("previews a text file inline via the artifact preview machinery", async () => {
    const { container } = render(
      <WorkspaceFilePanel threadId="t1" open={true} />,
    );
    await waitFor(() => {
      expect(byTestId(container, "workspace-files-list").textContent).toContain(
        "notes.md",
      );
    });
    fireEvent.click(getAllByText(container, "notes.md")[0]!);
    const dialog = await waitFor(() =>
      byTestId(container, "workspace-files-preview"),
    );
    // Markdown goes through the shared ArtifactFilePreview component.
    const preview = dialog.querySelector(
      '[data-testid="artifact-file-preview"]',
    );
    expect(preview).toBeTruthy();
    expect(preview?.getAttribute("data-language")).toBe("markdown");
    expect(preview?.textContent).toContain("Hello workspace");
    // A download affordance is present, pointing at the artifact endpoint.
    const downloadLink = dialog.querySelector('a[href*="download=true"]');
    expect(downloadLink).toBeTruthy();
  });

  it("renders code files with the readonly editor and media through the iframe branch", async () => {
    const { container } = render(
      <WorkspaceFilePanel threadId="t1" open={true} />,
    );
    await waitFor(() => {
      expect(byTestId(container, "workspace-files-list").textContent).toContain(
        "script.py",
      );
    });
    fireEvent.click(getAllByText(container, "script.py")[0]!);
    await waitFor(() => {
      expect(
        byTestId(container, "workspace-files-preview").querySelector(
          '[data-testid="code-editor"]',
        ),
      ).toBeTruthy();
    });

    cleanup();
    const media = render(<WorkspaceFilePanel threadId="t1" open={true} />);
    await waitFor(() => {
      expect(
        byTestId(media.container, "workspace-files-list").textContent,
      ).toContain("demo.mp4");
    });
    fireEvent.click(getAllByText(media.container, "demo.mp4")[0]!);
    await waitFor(() => {
      const iframe = byTestId(
        media.container,
        "workspace-files-preview",
      ).querySelector("iframe");
      expect(iframe).toBeTruthy();
      expect(iframe?.getAttribute("src")).toContain(
        "/api/threads/t1/artifacts/mnt/user-data/demo.mp4",
      );
      expect(iframe?.getAttribute("src")).not.toContain("download=true");
    });
  });

  it("falls back to download-only with a hint for oversized text and opens downloads for binaries", async () => {
    const openSpy = rs.fn();
    const originalOpen = window.open;
    window.open = openSpy as unknown as typeof window.open;
    try {
      const { container } = render(
        <WorkspaceFilePanel threadId="t1" open={true} />,
      );
      await waitFor(() => {
        expect(
          byTestId(container, "workspace-files-list").textContent,
        ).toContain("huge.csv");
      });

      // Oversized text: dialog with the too-large hint, no inline content.
      fireEvent.click(getAllByText(container, "huge.csv")[0]!);
      const dialog = await waitFor(() =>
        byTestId(container, "workspace-files-preview"),
      );
      expect(
        dialog.querySelector('[data-testid="workspace-files-too-large"]'),
      ).toBeTruthy();
      expect(dialog.querySelector('a[href*="download=true"]')).toBeTruthy();
      expect(
        dialog.querySelector('[data-testid="artifact-file-preview"]'),
      ).toBeNull();
      expect(useArtifactContent.mock.calls.at(-1)?.[0]?.enabled).toBe(false);

      // Binary: no dialog at all, straight to a download window.open.
      fireEvent.click(byTestId(container, "workspace-files-preview-close"));
      await waitFor(() => {
        expect(queryByTestId(container, "workspace-files-preview")).toBeNull();
      });
      fireEvent.click(getAllByText(container, "report.pdf")[0]!);
      expect(queryByTestId(container, "workspace-files-preview")).toBeNull();
      expect(openSpy).toHaveBeenCalledTimes(1);
      expect(String(openSpy.mock.calls[0]?.[0])).toContain(
        "/api/threads/t1/artifacts/mnt/user-data/report.pdf?download=true",
      );
    } finally {
      window.open = originalOpen;
    }
  });

  it("embedded variant renders regardless of `open` and fills its parent", async () => {
    const { container } = render(
      <WorkspaceFilePanel threadId="t1" open={false} variant="embedded" />,
    );
    await waitFor(() => {
      expect(queryByTestId(container, "workspace-files-list")).toBeTruthy();
    });
    expect(urlCalls()[0]).toBe(
      "http://gw/api/threads/t1/workspace/files?path=%2Fmnt%2Fuser-data",
    );
  });
});
