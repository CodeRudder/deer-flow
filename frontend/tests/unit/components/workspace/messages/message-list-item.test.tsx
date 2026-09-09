import type { Message } from "@langchain/langgraph-sdk";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { MessageListItem } from "@/components/workspace/messages/message-list-item";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: { uploads: { uploading: "uploading" } } }),
}));

afterEach(cleanup);

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
    // A mirrored clarification bubble (content + ask_clarification tool call)
    // keeps its reasoning in the processing group's ChainOfThought panel;
    // rendering it inside the bubble's <Reasoning> too would paint it twice
    // (#3868).
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
