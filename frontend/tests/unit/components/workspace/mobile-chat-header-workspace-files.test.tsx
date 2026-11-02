import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { MobileChatHeader } from "@/components/workspace/mobile/chat-header";
import { ArtifactsProvider } from "@/components/workspace/artifacts/context";

/**
 * Mount case for the mobile workspace-files entry point: the header renders a
 * folder button on every thread (the browser works with zero artifacts) and
 * taps call the page-provided opener, which mounts the overlay drawer.
 */

const mocks = rs.hoisted(() => ({ quota: { data: null, isPending: true, isError: false } }));

rs.mock("@/core/quotas/hooks", () => ({ useQuotaMe: () => mocks.quota }));

rs.mock("@/components/ui/sidebar", () => ({
  useSidebar: () => ({ setOpen: rs.fn() }),
}));

rs.mock("@/env", () => ({ env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" } }));

rs.mock("@/components/ui/sheet", () => ({
  Sheet: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SheetContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SheetHeader: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SheetTitle: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

rs.mock("@/components/workspace/session-status-dialog", () => ({
  SessionStatusButton: () => <button type="button" />,
}));
rs.mock("@/components/workspace/token-usage-indicator", () => ({
  TokenUsageIndicator: () => null,
}));
rs.mock("@/components/workspace/quota-indicator", () => ({
  QuotaIndicator: () => null,
}));
rs.mock("@/components/workspace/export-trigger", () => ({
  ExportTrigger: () => null,
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      pages: { chats: "Chats", newChat: "New chat", untitled: "Untitled" },
      common: { more: "More", artifacts: "Artifacts", close: "Close" },
      workspaceFiles: { toggle: "Toggle workspace files panel" },
      chats: { sessionStatus: "Session status" },
      tokenUsage: { title: "Token usage" },
      quotaIndicator: { label: "Quota" },
    },
  }),
}));

const thread = {
  messages: [],
  isLoading: false,
} as unknown as Parameters<typeof MobileChatHeader>[0]["thread"];

function Harness({ onOpen }: { onOpen: () => void }) {
  return (
    <ArtifactsProvider>
      <MobileChatHeader
        title="Test thread"
        threadId="t1"
        thread={thread}
        isNewThread={false}
        isMock={false}
        isBusy={false}
        pendingUsageMessages={[]}
        backendTokenUsage={null}
        tokenUsageEnabled={false}
        tokenUsagePreferences={{ inlineMode: "auto" } as never}
        onTokenUsagePreferencesChange={() => {}}
        onOpenWorkspaceFiles={onOpen}
      />
    </ArtifactsProvider>
  );
}

describe("MobileChatHeader workspace files entry", () => {
  afterEach(cleanup);

  it("renders the folder button unconditionally and opens the drawer on tap", () => {
    const onOpen = rs.fn();
    const { container } = render(<Harness onOpen={onOpen} />);

    const button = container.querySelector(
      '[data-testid="mobile-chat-workspace-files"]',
    ) as HTMLButtonElement | null;
    expect(button).toBeTruthy();

    fireEvent.click(button!);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("hides the entry point when no opener is provided", () => {
    const { container } = render(
      <ArtifactsProvider>
        <MobileChatHeader
          title="Test thread"
          threadId="t1"
          thread={thread}
          isNewThread={false}
          isMock={false}
          isBusy={false}
          pendingUsageMessages={[]}
          backendTokenUsage={null}
          tokenUsageEnabled={false}
          tokenUsagePreferences={{ inlineMode: "auto" } as never}
          onTokenUsagePreferencesChange={() => {}}
        />
      </ArtifactsProvider>,
    );
    expect(
      container.querySelector('[data-testid="mobile-chat-workspace-files"]'),
    ).toBeNull();
  });
});
