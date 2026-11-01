import { describe, expect, test } from "@rstest/core";

import {
  chatPath,
  DEFAULT_CHAT_BASE_PATH,
} from "@/components/workspace/chats/chat-path";

describe("chatPath", () => {
  test("defaults to the desktop tree", () => {
    expect(DEFAULT_CHAT_BASE_PATH).toBe("/workspace");
    expect(chatPath(DEFAULT_CHAT_BASE_PATH, "abc")).toBe(
      "/workspace/chats/abc",
    );
  });

  test("omitting the thread id targets the new-chat route", () => {
    expect(chatPath(DEFAULT_CHAT_BASE_PATH)).toBe("/workspace/chats/new");
  });

  test("honours an alternate prefix", () => {
    expect(chatPath("/m/workspace", "abc")).toBe("/m/workspace/chats/abc");
    expect(chatPath("/m/workspace")).toBe("/m/workspace/chats/new");
  });

  test("normalizes a missing or trailing slash", () => {
    expect(chatPath("workspace", "abc")).toBe("/workspace/chats/abc");
    expect(chatPath("/workspace/", "abc")).toBe("/workspace/chats/abc");
    expect(chatPath("/m/workspace///", "abc")).toBe("/m/workspace/chats/abc");
  });

  test("keeps thread ids verbatim", () => {
    // Backend ids are uuids; no encoding step is applied (behaviour parity
    // with the hardcoded paths this helper replaced).
    expect(chatPath("/workspace", "0b1c-2d3e")).toBe(
      "/workspace/chats/0b1c-2d3e",
    );
  });
});
