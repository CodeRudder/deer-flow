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
    fireEvent.click(
      document.querySelector('[aria-label="Zoom in"]')!,
    );
    expect(document.body.textContent).toContain("125%");
    fireEvent.click(
      document.querySelector('[aria-label="Zoom out"]')!,
    );
    expect(document.body.textContent).toContain("100%");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    unmount();
  });
});
