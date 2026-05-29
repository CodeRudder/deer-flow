import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const mockUseThreads = vi.fn();

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      pages: { chats: "Chats", appName: "DeerFlow" },
      chats: { searchChats: "Search chats" },
      breadcrumb: { workspace: "Workspace", chats: "Chats" },
      common: { home: "Home" },
    },
  }),
}));

vi.mock("@/core/threads/hooks", () => ({
  useThreads: () => mockUseThreads(),
}));

vi.mock("@/core/threads/utils", () => ({
  pathOfThread: (threadId: string) => `/workspace/chats/${threadId}`,
  titleOfThread: (thread: { values?: { title?: string } }) =>
    thread.values?.title ?? "Untitled",
}));

vi.mock("@/core/utils/datetime", () => ({
  formatTimeAgo: (value: string) => `ago:${value}`,
}));

vi.mock("@/components/ui/input", () => ({
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => (
    <input {...props} />
  ),
}));

vi.mock("@/components/ui/scroll-area", () => ({
  ScrollArea: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("@/components/workspace/workspace-container", () => ({
  WorkspaceBody: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  WorkspaceContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  WorkspaceHeader: () => <div />,
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/workspace/chats",
}));

vi.mock("@/env", () => ({
  env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" },
}));

import ChatsPage from "./page";

describe("ChatsPage", () => {
  it("renders created_at instead of updated_at for the list timestamp", () => {
    mockUseThreads.mockReturnValue({
      data: [
        {
          thread_id: "thread-1",
          created_at: "2026-05-28T08:00:00Z",
          updated_at: "2026-05-29T08:00:00Z",
          values: { title: "Old chat" },
        },
      ],
    });

    render(<ChatsPage />);

    expect(screen.getByText("ago:2026-05-28T08:00:00Z")).toBeTruthy();
    expect(screen.queryByText("ago:2026-05-29T08:00:00Z")).toBeNull();
  });
});
