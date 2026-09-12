import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import {
  isThreadGenerating,
  ThreadRow,
} from "@/components/workspace/mobile/thread-row";
import type { AgentThread } from "@/core/threads/types";

/**
 * Prototype ①'s「● 生成中」row state.
 *
 * The state is derived from `thread.status`, which the thread list already
 * carries: the gateway's `POST /api/threads/search` returns `ThreadResponse`
 * with a per-thread `status`, written `running` while a run is in flight and
 * reset at the end of the run. The desktop sidebar has no equivalent, so this
 * row state is the only place it surfaces — hence the render-level assertions
 * next to the pure predicate.
 */

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      chats: { generating: "Generating", actions: "Conversation actions" },
      common: {
        rename: "Rename",
        cancel: "Cancel",
        save: "Save",
        delete: "Delete",
      },
    },
    locale: "en-US",
  }),
}));

afterEach(cleanup);

const PREVIEW = "量子计算利用叠加与纠缠两个特性";

function buildThread({
  status,
  title = "帮我写个周报",
  preview = PREVIEW,
}: {
  status: string;
  title?: string;
  preview?: string | null;
}): AgentThread {
  return {
    thread_id: "11111111-1111-1111-1111-111111111111",
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
    status,
    metadata: {},
    interrupts: {},
    values: {
      title,
      messages:
        preview === null
          ? []
          : [{ type: "human", id: "m-1", content: preview }],
      artifacts: [],
      todos: [],
    },
  } as AgentThread;
}

function renderRow(thread: AgentThread) {
  return render(
    <ThreadRow
      thread={thread}
      pinned={false}
      onTogglePin={() => undefined}
      onRename={() => undefined}
      onDelete={() => undefined}
    />,
  );
}

/**
 * The wire status is a run-lifecycle word (`running`, `idle`, `error`, …),
 * wider than the SDK's `ThreadStatus` union — which is the point of the
 * predicate, so the fixtures are typed as plain strings.
 */
function threadWithStatus(status: string): Pick<AgentThread, "status"> {
  return { status } as Pick<AgentThread, "status">;
}

describe("isThreadGenerating", () => {
  it("treats the gateway's in-flight status as generating", () => {
    // `running` is what `threads_meta.status` holds between run creation and
    // run completion.
    expect(isThreadGenerating(threadWithStatus("running"))).toBe(true);
  });

  it("also accepts the LangGraph Platform spelling used by the optimistic upsert", () => {
    // `useThreadStream`'s onCreated writes `busy` into the thread caches.
    expect(isThreadGenerating(threadWithStatus("busy"))).toBe(true);
  });

  it.each(["idle", "error", "timeout", "interrupted"])(
    "does not treat the terminal status %s as generating",
    (status) => {
      expect(isThreadGenerating(threadWithStatus(status))).toBe(false);
    },
  );
});

describe("ThreadRow generating state", () => {
  it("marks a running thread with the state label and the accent row", () => {
    const { container } = renderRow(buildThread({ status: "running" }));

    const row = screen.getByTestId("mobile-thread-row");
    expect(row.textContent).toContain("Generating");
    expect(screen.getByTestId("mobile-thread-generating").textContent).toBe(
      "●Generating",
    );
    expect(container.querySelector("li")?.className).toContain("bg-accent");
  });

  it("leaves a settled thread unmarked", () => {
    const { container } = renderRow(buildThread({ status: "idle" }));

    expect(screen.queryByTestId("mobile-thread-generating")).toBeNull();
    expect(screen.getByTestId("mobile-thread-row").textContent).not.toContain(
      "Generating",
    );
    expect(container.querySelector("li")?.className).not.toContain("bg-accent");
  });

  it("still shows the state for a running thread with nothing to preview", () => {
    // The first run of a brand-new chat: no message has landed yet, so the
    // preview is empty and the state label is the only progress signal.
    renderRow(buildThread({ status: "running", preview: null }));

    expect(screen.getByTestId("mobile-thread-generating")).toBeTruthy();
  });

  it("keeps the preview alongside the state label", () => {
    renderRow(buildThread({ status: "running" }));

    expect(screen.getByText(PREVIEW)).toBeTruthy();
    expect(screen.getByTestId("mobile-thread-generating")).toBeTruthy();
  });
});
