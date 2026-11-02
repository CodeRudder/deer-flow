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
      common: { close: "Close", retry: "Retry" },
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
      },
    },
  }),
}));

rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "http://gw" }));

const fetchMock = rs.hoisted(() => rs.fn());
rs.mock("@/core/api/fetcher", () => ({ fetch: fetchMock }));

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
});
