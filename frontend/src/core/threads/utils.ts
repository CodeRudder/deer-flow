import type { Message } from "@langchain/langgraph-sdk";

import type { AgentThread } from "./types";

export function pathOfThread(threadId: string) {
  return `/workspace/chats/${threadId}`;
}

export function textOfMessage(message: Message) {
  if (typeof message.content === "string") {
    return message.content;
  } else if (Array.isArray(message.content)) {
    for (const part of message.content) {
      if (part.type === "text") {
        return part.text;
      }
    }
  }
  return null;
}

export function titleOfThread(thread: AgentThread) {
  return thread.values?.title ?? "Untitled";
}

export function lastMessagePreview(thread: AgentThread, maxLen = 60): string | null {
  const messages = thread.values?.messages;
  if (!messages || !Array.isArray(messages) || messages.length === 0) return null;
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]!;
    if (msg.type !== "human" && msg.type !== "ai") continue;
    const text = textOfMessage(msg);
    if (text && text.trim()) {
      const clean = text.trim().replace(/\s+/g, " ");
      return clean.length > maxLen ? clean.slice(0, maxLen) + "…" : clean;
    }
  }
  return null;
}
