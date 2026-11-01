import type { Message } from "@langchain/langgraph-sdk";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";

import { MessageListItem } from "@/components/workspace/messages/message-list-item";
import * as messageUtils from "@/core/messages/utils";

// Count the toolbar copy derivation via its entry point
// (`stripUploadedFilesTag`); other render paths never call it.
const copyDerivations = rs.spyOn(messageUtils, "stripUploadedFilesTag");

const clipboardWrites: string[] = [];

// Everything but the namespaces the assertions below care about is stubbed:
// the reasoning trigger reads `toolCalls` (T19), and rendering these rows must
// not blow up on a double that only declares what it asserts.
rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      uploads: { uploading: "uploading" },
      clipboard: {
        copyToClipboard: "copy",
        failedToCopyToClipboard: "failed to copy",
      },
      toolCalls: {
        thinking: "thinking",
        thoughtFor: (duration: string) => `thought for ${duration}`,
        thoughtBriefly: "thought briefly",
        durationSeconds: (seconds: number) => `${seconds}s`,
        durationMinutes: (minutes: number, seconds: number) =>
          `${minutes}m ${seconds}s`,
      },
    },
  }),
}));

rs.mock("@/core/clipboard", () => ({
  writeTextToClipboard: async (text: string) => {
    clipboardWrites.push(text);
    return true;
  },
  // streamdown.tsx patches browser globals at import time; no-op it in tests.
  installClipboardFallback: () => undefined,
}));

afterEach(cleanup);
afterEach(() => {
  copyDerivations.mockClear();
});

function humanMessageWithImages(): Message {
  return {
    id: "m1",
    type: "human",
    content: "看看这些图",
    additional_kwargs: {
      files: [
        {
          filename: "a.png",
          size: 1,
          path: "/mnt/user-data/a.png",
          status: "uploaded",
        },
        {
          filename: "b.png",
          size: 1,
          path: "/mnt/user-data/b.png",
          status: "uploaded",
        },
      ],
    },
  } as unknown as Message;
}

describe("MessageListItem reasoning placement", () => {
  it("does not duplicate reasoning inside a tool-calling clarification bubble", () => {
    // Reasoning stays in the CoT panel for tool-calling bubbles (#3868).
    const message = {
      id: "ai-clarification",
      type: "ai",
      content: "Before I continue, which city?",
      additional_kwargs: { reasoning_content: "internal reasoning" },
      tool_calls: [
        {
          id: "call-1",
          name: "ask_clarification",
          args: { question: "Which city?" },
        },
      ],
    } as unknown as Message;

    const { container } = render(
      <MessageListItem
        message={message}
        showCopyButton={false}
        threadId="t1"
      />,
    );

    expect(container.textContent).toContain("Before I continue, which city?");
    expect(container.textContent).not.toContain("internal reasoning");
  });

  it("keeps reasoning inside a plain assistant bubble", () => {
    const message = {
      id: "ai-1",
      type: "ai",
      content: "Rayleigh scattering makes the sky blue.",
      additional_kwargs: { reasoning_content: "internal reasoning" },
    } as unknown as Message;

    const { container } = render(
      <MessageListItem
        message={message}
        showCopyButton={false}
        threadId="t1"
      />,
    );

    expect(container.textContent).toContain(
      "Rayleigh scattering makes the sky blue.",
    );
    expect(container.textContent).toContain("internal reasoning");
  });
});

describe("MessageListItem reasoning trigger wording", () => {
  it("feeds the trigger the locale strings instead of the registry defaults", () => {
    // T19: the trigger's hardcoded English is replaced by the injected
    // callback at this call site; the stub above makes the swap observable.
    const fallback = render(
      <MessageListItem
        message={
          {
            id: "ai-reasoning-only",
            type: "ai",
            content: "",
            additional_kwargs: { reasoning_content: "internal reasoning" },
          } as unknown as Message
        }
        showCopyButton={false}
        threadId="t-reasoning"
      />,
    );

    expect(fallback.container.textContent).toContain("thought briefly");
    expect(fallback.container.textContent).not.toContain(
      "Thought for a few seconds",
    );
    cleanup();

    const settled = render(
      <MessageListItem
        message={
          {
            id: "ai-reasoning-timed",
            type: "ai",
            content: "",
            additional_kwargs: {
              reasoning_content: "internal reasoning",
              turn_duration: 12,
            },
          } as unknown as Message
        }
        showCopyButton={false}
        threadId="t-reasoning"
      />,
    );

    expect(settled.container.textContent).toContain("thought for 12s");
    expect(settled.container.textContent).not.toContain("Thought for 12s");
  });
});

describe("MessageListItem human message copy", () => {
  it("copies the displayed text without the injected <uploaded_files> block", async () => {
    clipboardWrites.length = 0;
    const message = {
      id: "human-uploaded",
      type: "human",
      content:
        "<uploaded_files>\nThe following files were uploaded in this message:\n\n(empty)\n\nThe following files were uploaded in previous messages and are still available:\n\n- 视频生成工作流.md (14.5 KB)\n  Path: /mnt/user-data/uploads/视频生成工作流.md\n</uploaded_files>\n\n先撤回到mermaid版本",
    } as unknown as Message;

    const { container } = render(
      <MessageListItem message={message} threadId="t1" />,
    );

    fireEvent.click(container.querySelector("button")!);

    await waitFor(() => {
      expect(clipboardWrites.length).toBe(1);
    });
    expect(clipboardWrites[0]).toBe("先撤回到mermaid版本");
  });
});

describe("MessageListItem uploaded image cards", () => {
  it("opens the lightbox from an uploaded image card", () => {
    const { container, unmount } = render(
      <MessageListItem
        message={humanMessageWithImages()}
        showCopyButton={false}
        threadId="t1"
      />,
    );
    const cards = container.querySelectorAll("button img");
    expect(cards.length).toBe(2);
    fireEvent.click(cards[0]!.closest("button")!);
    expect(document.querySelector('[role="dialog"]')).toBeTruthy();
    expect(document.body.textContent).toContain("1 / 2");
    expect(
      document.querySelector<HTMLImageElement>('[role="dialog"] img')?.src,
    ).toContain("a.png");
    unmount();
  });

  it("supports zoom controls and closes with Escape", () => {
    const { container, unmount } = render(
      <MessageListItem
        message={humanMessageWithImages()}
        showCopyButton={false}
        threadId="t1"
      />,
    );
    fireEvent.click(container.querySelector("button img")!);
    fireEvent.click(document.querySelector('[aria-label="Zoom in"]')!);
    expect(document.body.textContent).toContain("125%");
    fireEvent.click(document.querySelector('[aria-label="Zoom out"]')!);
    expect(document.body.textContent).toContain("100%");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    unmount();
  });
});

describe("MessageListItem copy-data derivation guard", () => {
  it("derives the toolbar copy text once per settled row", () => {
    // Same message reference (a chunk re-render) must reuse the memo (#5094).
    const message = {
      id: "ai-copy",
      type: "ai",
      content: "copy me",
    } as unknown as Message;

    const { rerender } = render(
      <MessageListItem message={message} threadId="t1" />,
    );

    expect(copyDerivations).toHaveBeenCalledTimes(1);

    rerender(<MessageListItem message={message} threadId="t1" />);

    expect(copyDerivations).toHaveBeenCalledTimes(1);
  });

  it("re-derives when the row receives a new message reference", () => {
    const first = {
      id: "ai-1",
      type: "ai",
      content: "first",
    } as unknown as Message;
    const second = {
      id: "ai-1",
      type: "ai",
      content: "second",
    } as unknown as Message;

    const { rerender } = render(
      <MessageListItem message={first} threadId="t1" />,
    );
    expect(copyDerivations).toHaveBeenCalledTimes(1);

    rerender(<MessageListItem message={second} threadId="t1" />);
    expect(copyDerivations).toHaveBeenCalledTimes(2);
  });

  it("skips the derivation when the toolbar never renders", () => {
    // No copy button (opt-out or loading) means no derivation.
    const message = {
      id: "ai-no-toolbar",
      type: "ai",
      content: "copy me",
    } as unknown as Message;

    const { unmount } = render(
      <MessageListItem
        message={message}
        showCopyButton={false}
        threadId="t1"
      />,
    );
    expect(copyDerivations).not.toHaveBeenCalled();
    unmount();

    render(<MessageListItem message={message} isLoading threadId="t1" />);
    expect(copyDerivations).not.toHaveBeenCalled();
  });
});
