import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";

import { ArtifactsProvider, useArtifacts } from "@/components/workspace/artifacts/context";
import { WORKSPACE_PANEL_TAB_KEY } from "@/components/workspace/artifacts/context";

rs.mock("@/components/ui/sidebar", () => ({
  useSidebar: () => ({ setOpen: rs.fn() }),
}));

rs.mock("@/env", () => ({ env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" } }));

function TabProbe() {
  const { panelTab, setPanelTab } = useArtifacts();
  return (
    <div>
      <span data-testid="tab">{panelTab}</span>
      <button
        type="button"
        data-testid="to-files"
        onClick={() => setPanelTab("files")}
      />
    </div>
  );
}

describe("ArtifactsProvider panel tab", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(cleanup);

  it("defaults to artifacts and persists a tab switch to localStorage", async () => {
    const { container, unmount } = render(
      <ArtifactsProvider>
        <TabProbe />
      </ArtifactsProvider>,
    );
    expect((container.querySelector('[data-testid="tab"]') as HTMLElement).textContent).toBe("artifacts");

    fireEvent.click(container.querySelector('[data-testid="to-files"]')!);
    await waitFor(() => {
      expect(
        (container.querySelector('[data-testid="tab"]') as HTMLElement)
          .textContent,
      ).toBe("files");
    });
    expect(window.localStorage.getItem(WORKSPACE_PANEL_TAB_KEY)).toBe("files");

    // A fresh mount restores the persisted tab.
    unmount();
    const revived = render(
      <ArtifactsProvider>
        <TabProbe />
      </ArtifactsProvider>,
    );
    await waitFor(() => {
      expect(
        (revived.container.querySelector('[data-testid="tab"]') as HTMLElement)
          .textContent,
      ).toBe("files");
    });
  });
});
