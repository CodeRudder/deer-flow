import type { AIMessage, Message } from "@langchain/langgraph-sdk";

interface GenericMessageGroup<T = string> {
  type: T;
  id: string | undefined;
  messages: Message[];
}

interface HumanMessageGroup extends GenericMessageGroup<"human"> {}

interface AssistantProcessingGroup extends GenericMessageGroup<"assistant:processing"> {}

interface AssistantMessageGroup extends GenericMessageGroup<"assistant"> {}

interface AssistantPresentFilesGroup extends GenericMessageGroup<"assistant:present-files"> {}

interface AssistantClarificationGroup extends GenericMessageGroup<"assistant:clarification"> {}

interface AssistantSubagentGroup extends GenericMessageGroup<"assistant:subagent"> {}

export type MessageGroup =
  | HumanMessageGroup
  | AssistantProcessingGroup
  | AssistantMessageGroup
  | AssistantPresentFilesGroup
  | AssistantClarificationGroup
  | AssistantSubagentGroup;

const HIDDEN_CONTROL_MESSAGE_NAMES = new Set([
  "summary",
  "loop_warning",
  "todo_reminder",
  "todo_completion_reminder",
]);

export function getMessageGroups(
  messages: Message[],
  { isCurrentTurnLoading = false }: { isCurrentTurnLoading?: boolean } = {},
): MessageGroup[] {
  if (messages.length === 0) {
    return [];
  }

  const groups: MessageGroup[] = [];
  const toolCallGroupMap = new Map<string, MessageGroup>();

  // While loading, AI messages after the last visible human message are
  // still unresolved: providers may append tool-call chunks later (#4304).
  let currentTurnStartIndex = -1;
  if (isCurrentTurnLoading) {
    for (let index = messages.length - 1; index >= 0; index--) {
      const message = messages[index];
      if (message?.type === "human" && !isHiddenFromUIMessage(message)) {
        currentTurnStartIndex = index;
        break;
      }
    }
  }

  // Returns the last group if it can still accept tool messages
  // (i.e. it's an in-flight processing group, not a terminal human/assistant group).
  function lastOpenGroup() {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.type !== "human" &&
      last.type !== "assistant" &&
      last.type !== "assistant:clarification"
    ) {
      return last;
    }
    return null;
  }

  function registerToolCalls(message: AIMessage, group: MessageGroup) {
    for (const toolCall of message.tool_calls ?? []) {
      if (toolCall.id) {
        toolCallGroupMap.set(toolCall.id, group);
      }
    }
  }

  for (const [messageIndex, message] of messages.entries()) {
    if (isHiddenFromUIMessage(message)) {
      continue;
    }

    if (message.type === "human") {
      groups.push({ id: message.id, type: "human", messages: [message] });
      continue;
    }

    if (message.type === "tool") {
      if (isClarificationToolMessage(message)) {
        // Add to the preceding processing group to preserve tool-call association,
        // then also open a standalone clarification group for prominent display.
        const parent = message.tool_call_id
          ? (toolCallGroupMap.get(message.tool_call_id) ?? lastOpenGroup())
          : lastOpenGroup();
        parent?.messages.push(message);
        groups.push({
          id: message.id,
          type: "assistant:clarification",
          messages: [message],
        });
      } else {
        const open = message.tool_call_id
          ? (toolCallGroupMap.get(message.tool_call_id) ?? lastOpenGroup())
          : lastOpenGroup();
        if (open) {
          open.messages.push(message);
        }
      }
      continue;
    }

    if (message.type === "ai") {
      // Streaming text may still gain tool-call chunks later (#4304): keep
      // it in the processing group until the turn settles.
      const isUnresolvedAssistantText =
        currentTurnStartIndex >= 0 &&
        messageIndex > currentTurnStartIndex &&
        hasContent(message) &&
        !hasToolCalls(message);

      // Content-bearing bubbles own their reasoning; never also feed the
      // processing group or the CoT panel paints it twice (#3868).
      const becomesAssistantBubble =
        hasContent(message) &&
        !hasToolCalls(message) &&
        !isUnresolvedAssistantText;

      if (hasPresentFiles(message)) {
        const group: MessageGroup = {
          id: message.id,
          type: "assistant:present-files",
          messages: [message],
        };
        groups.push(group);
        registerToolCalls(message, group);
      } else if (hasSubagent(message)) {
        const group: MessageGroup = {
          id: message.id,
          type: "assistant:subagent",
          messages: [message],
        };
        groups.push(group);
        registerToolCalls(message, group);
      } else if (
        !becomesAssistantBubble &&
        (hasReasoning(message) ||
          hasToolCalls(message) ||
          isUnresolvedAssistantText)
      ) {
        const lastGroup = groups[groups.length - 1];
        let group: MessageGroup;
        // Accumulate consecutive intermediate AI messages into one processing group.
        if (lastGroup?.type !== "assistant:processing") {
          group = {
            id: message.id,
            type: "assistant:processing",
            messages: [message],
          };
          groups.push(group);
        } else {
          lastGroup.messages.push(message);
          group = lastGroup;
        }
        registerToolCalls(message, group);
      }

      const hasClarificationToolCall = hasToolCallNamed(
        message,
        "ask_clarification",
      );

      // A tool-calling clarification message keeps its processing steps above
      // AND gets an assistant bubble carrying the question text.
      if (
        becomesAssistantBubble ||
        (hasClarificationToolCall && hasDisplayableContent(message))
      ) {
        groups.push({ id: message.id, type: "assistant", messages: [message] });
      }
    }
  }

  return groups;
}

export function groupMessages<T>(
  messages: Message[],
  mapper: (group: MessageGroup) => T,
): T[] {
  return getMessageGroups(messages)
    .map(mapper)
    .filter((result) => result !== undefined && result !== null) as T[];
}

export function getAssistantTurnUsageMessages(groups: MessageGroup[]) {
  const usageMessagesByGroupIndex: Array<Message[] | null> = Array.from(
    { length: groups.length },
    () => null,
  );

  let turnStartIndex: number | null = null;

  for (const [index, group] of groups.entries()) {
    if (group.type === "human") {
      turnStartIndex = null;
      continue;
    }

    turnStartIndex ??= index;

    const nextGroup = groups[index + 1];
    const isTurnEnd = !nextGroup || nextGroup.type === "human";

    if (!isTurnEnd) {
      continue;
    }

    usageMessagesByGroupIndex[index] = groups
      .slice(turnStartIndex, index + 1)
      .flatMap((currentGroup) => currentGroup.messages)
      .filter((message) => message.type === "ai");

    turnStartIndex = null;
  }

  return usageMessagesByGroupIndex;
}

type MessageMetadataLookup = (
  message: Message,
  index: number,
) => { streamMetadata?: Record<string, unknown> } | undefined;

export type StreamingMessageLookup = {
  ids: ReadonlySet<string>;
  messages: ReadonlySet<Message>;
};

export function getStreamingMessageLookup(
  messages: Message[],
  isStreaming: boolean,
  getMessagesMetadata?: MessageMetadataLookup,
): StreamingMessageLookup {
  const streamingMessageIds = new Set<string>();
  const streamingMessages = new Set<Message>();

  if (!isStreaming) {
    return {
      ids: streamingMessageIds,
      messages: streamingMessages,
    };
  }

  messages.forEach((message, index) => {
    if (!getMessagesMetadata?.(message, index)?.streamMetadata) {
      return;
    }

    if (typeof message.id === "string" && message.id.length > 0) {
      streamingMessageIds.add(message.id);
    }
    streamingMessages.add(message);
  });

  return {
    ids: streamingMessageIds,
    messages: streamingMessages,
  };
}

export function isAssistantMessageGroupStreaming(
  groupMessages: Message[],
  streamingMessages: StreamingMessageLookup,
) {
  return groupMessages.some((message) => {
    if (message.type !== "ai") {
      return false;
    }

    return (
      (typeof message.id === "string" &&
        message.id.length > 0 &&
        streamingMessages.ids.has(message.id)) ||
      streamingMessages.messages.has(message)
    );
  });
}

// Cache per array reference: the copy button re-reads the settled array on
// every message-list render (#5094). A rebuilt array misses and recomputes, so
// the cache never serves a stale value.
const assistantTurnCopyDataCache = new WeakMap<Message[], string>();

export function getAssistantTurnCopyData(
  messages: Message[],
  { isStreaming = false }: { isStreaming?: boolean } = {},
) {
  if (isStreaming) {
    return null;
  }

  const cached = assistantTurnCopyDataCache.get(messages);
  if (cached !== undefined) {
    return cached;
  }

  const copyData =
    [...messages]
      .reverse()
      .filter((message) => message.type === "ai")
      .map((message) => {
        // extractContentFromMessage never returns null: fall back to reasoning
        // on empty text, or a reasoning-only turn loses its copy button.
        const content = extractContentFromMessage(message);
        return content.length > 0
          ? content
          : (extractReasoningContentFromMessage(message) ?? "");
      })
      .find((content) => content.length > 0) ?? null;
  if (copyData !== null) {
    assistantTurnCopyDataCache.set(messages, copyData);
  }
  return copyData;
}

export function extractTextFromMessage(message: Message) {
  if (typeof message.content === "string") {
    return (
      splitInlineReasoningFromAIMessage(message)?.content ??
      message.content.trim()
    );
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((content) =>
        typeof content === "string"
          ? content
          : content.type === "text"
            ? content.text
            : "",
      )
      .join("\n")
      .trim();
  }
  return "";
}

const THINK_OPEN_TAG = "<think>";
const THINK_TAG_RE = /<think>\s*([\s\S]*?)\s*<\/think>/g;

interface InlineReasoningSplit {
  content: string;
  reasoning: string | null;
}

function splitInlineReasoning(content: string): InlineReasoningSplit {
  const reasoningParts: string[] = [];

  // First pass: strip every fully closed `<think>...</think>` pair and
  // collect its body as reasoning.
  let cleaned = content.replace(THINK_TAG_RE, (_, reasoning: string) => {
    const normalized = reasoning.trim();
    if (normalized) {
      reasoningParts.push(normalized);
    }
    return "";
  });

  // Streaming-safe pass: a `<think>` opener whose `</think>` has not arrived
  // yet means the rest of the chunk is reasoning in flight. Route it into the
  // reasoning slot instead of letting it render as message content (the
  // raw-HTML markdown pipeline would otherwise paint the inner text on
  // screen until the closing tag lands).
  //
  // Skip when the opener sits right after a backtick — that is the model
  // talking about `<think>` literally inside markdown inline code, not
  // actually streaming reasoning.
  const openTagIndex = cleaned.indexOf(THINK_OPEN_TAG);
  if (openTagIndex !== -1 && cleaned[openTagIndex - 1] !== "`") {
    const tail = cleaned.slice(openTagIndex + THINK_OPEN_TAG.length).trim();
    if (tail) {
      reasoningParts.push(tail);
    }
    cleaned = cleaned.slice(0, openTagIndex);
  }

  return {
    content: cleaned.trim(),
    reasoning: reasoningParts.length > 0 ? reasoningParts.join("\n\n") : null,
  };
}

// Cache per message object, keyed by the exact content string so reassignment
// recomputes. The split is re-derived every render and its consumers scan the
// whole list per stream chunk — quadratic across a long run.
const inlineReasoningCache = new WeakMap<
  object,
  { content: string; split: InlineReasoningSplit }
>();

function splitInlineReasoningFromAIMessage(message: Message) {
  if (message.type !== "ai" || typeof message.content !== "string") {
    return null;
  }
  const content = message.content;
  const cached = inlineReasoningCache.get(message);
  if (cached?.content === content) {
    return cached.split;
  }
  const split = splitInlineReasoning(content);
  inlineReasoningCache.set(message, { content, split });
  return split;
}

export function extractContentFromMessage(message: Message) {
  if (typeof message.content === "string") {
    return (
      splitInlineReasoningFromAIMessage(message)?.content ??
      message.content.trim()
    );
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((content) => {
        if (typeof content === "string") {
          return content;
        }
        switch (content.type) {
          case "text":
            return content.text;
          case "image_url":
            const imageURL = extractURLFromImageURLContent(content.image_url);
            return `![image](${imageURL})`;
          default:
            return "";
        }
      })
      .join("\n")
      .trim();
  }
  return "";
}

export function extractReasoningContentFromMessage(message: Message) {
  if (message.type !== "ai") {
    return null;
  }
  if (
    message.additional_kwargs &&
    "reasoning_content" in message.additional_kwargs
  ) {
    return message.additional_kwargs.reasoning_content as string | null;
  }
  if (Array.isArray(message.content)) {
    const part = message.content[0];
    if (part && typeof part === "object" && "thinking" in part) {
      return part.thinking as string;
    }
  }
  if (typeof message.content === "string") {
    return splitInlineReasoningFromAIMessage(message)?.reasoning ?? null;
  }
  return null;
}

export function removeReasoningContentFromMessage(message: Message) {
  if (message.type !== "ai" || !message.additional_kwargs) {
    return;
  }
  delete message.additional_kwargs.reasoning_content;
}

export function extractURLFromImageURLContent(
  content:
    | string
    | {
        url: string;
      },
) {
  if (typeof content === "string") {
    return content;
  }
  return content.url;
}

export function hasContent(message: Message) {
  if (typeof message.content === "string") {
    return (
      (
        splitInlineReasoningFromAIMessage(message)?.content ??
        message.content.trim()
      ).length > 0
    );
  }
  if (Array.isArray(message.content)) {
    return message.content.length > 0;
  }
  return false;
}

export function hasReasoning(message: Message) {
  if (message.type !== "ai") {
    return false;
  }
  if (typeof message.additional_kwargs?.reasoning_content === "string") {
    return true;
  }
  if (Array.isArray(message.content)) {
    const part = message.content[0];
    // Compatible with the Anthropic gateway
    return (part as unknown as { type: "thinking" })?.type === "thinking";
  }
  if (typeof message.content === "string") {
    return (
      (splitInlineReasoningFromAIMessage(message)?.reasoning ?? null) !== null
    );
  }
  return false;
}

export function hasToolCalls(message: Message) {
  return (
    message.type === "ai" && message.tool_calls && message.tool_calls.length > 0
  );
}

function hasToolCallNamed(message: Message, toolName: string) {
  return (
    message.type === "ai" &&
    message.tool_calls?.some((toolCall) => toolCall.name === toolName) === true
  );
}

function hasDisplayableContent(message: Message) {
  return extractContentFromMessage(message).length > 0;
}

export function hasPresentFiles(message: Message) {
  return (
    message.type === "ai" &&
    message.tool_calls?.some((toolCall) => toolCall.name === "present_files")
  );
}

export function isClarificationToolMessage(message: Message) {
  return message.type === "tool" && message.name === "ask_clarification";
}

export function extractPresentFilesFromMessage(message: Message) {
  if (message.type !== "ai" || !hasPresentFiles(message)) {
    return [];
  }
  const files: string[] = [];
  for (const toolCall of message.tool_calls ?? []) {
    if (
      toolCall.name === "present_files" &&
      Array.isArray(toolCall.args.filepaths)
    ) {
      files.push(...(toolCall.args.filepaths as string[]));
    }
  }
  return files;
}

export function hasSubagent(message: AIMessage) {
  for (const toolCall of message.tool_calls ?? []) {
    if (toolCall.name === "task") {
      return true;
    }
  }
  return false;
}

export function findToolCallResult(toolCallId: string, messages: Message[]) {
  for (const message of messages) {
    if (message.type === "tool" && message.tool_call_id === toolCallId) {
      const content = extractTextFromMessage(message);
      if (content) {
        return content;
      }
    }
  }
  return undefined;
}

export function isHiddenFromUIMessage(message: Message) {
  if (message.additional_kwargs?.hide_from_ui === true) {
    return true;
  }
  if (
    typeof message.name === "string" &&
    HIDDEN_CONTROL_MESSAGE_NAMES.has(message.name)
  ) {
    return true;
  }
  // Only the human branch needs the text; extracting up front made every
  // discarded AI message pay a full content scan per stream chunk.
  if (message.type !== "human") {
    return false;
  }
  const content = extractTextFromMessage(message);
  return (
    content.includes("<slash_skill_activation>") &&
    stripUploadedFilesTag(content).length === 0
  );
}

/**
 * Represents a file stored in message additional_kwargs.files.
 * Used for optimistic UI (uploading state) and structured file metadata.
 */
export interface FileInMessage {
  filename: string;
  size: number; // bytes
  path?: string; // virtual path, may not be set during upload
  status?: "uploading" | "uploaded";
}

/**
 * Strip backend-injected human context tags from message content.
 * Kept under its historical name because callers use it for uploaded-file
 * display cleanup.
 */
export function stripUploadedFilesTag(content: string): string {
  return content
    .replace(/<(uploaded_files|slash_skill_activation)>[\s\S]*?<\/\1>/g, "")
    .trim();
}

/**
 * Tag names that backend middlewares wrap around internal payloads before
 * letting them ride along inside LangGraph message ``content``.
 *
 * These markers are *not* user copy — they come from:
 *
 * - ``UploadsMiddleware`` → ``<uploaded_files>``
 * - ``SkillActivationMiddleware`` → ``<slash_skill_activation>``
 * - ``DynamicContextMiddleware`` → ``<system-reminder>`` (carrying
 *   ``<memory>`` / ``<current_date>`` inside)
 * - ``TodoListMiddleware`` / ``LoopDetectionMiddleware`` style reminders
 *   live in ``hide_from_ui`` HumanMessages, but their inner payload uses
 *   the same tag vocabulary.
 *
 * The primary export filter is {@link isHiddenFromUIMessage}. This list is
 * the defence-in-depth strip for any message that — by middleware bug,
 * provider quirk, or merge-conflict regression — slips through without
 * its ``hide_from_ui`` flag set.
 */
export const INTERNAL_MARKER_TAGS = [
  "uploaded_files",
  "slash_skill_activation",
  "system-reminder",
  "memory",
  "current_date",
] as const;

const INTERNAL_MARKER_RE = new RegExp(
  `<(${INTERNAL_MARKER_TAGS.join("|")})>[\\s\\S]*?</\\1>`,
  "g",
);

/**
 * Strip every known backend-injected marker from message content.
 *
 * Intended for the chat export path where a marker leaking through is a
 * privacy regression. UI render paths should keep using
 * {@link stripUploadedFilesTag} — they receive ``hide_from_ui`` messages
 * via a separate filter and the narrower function avoids stripping content
 * a user might legitimately type into a meta-discussion (e.g. asking the
 * model about its own ``<memory>`` system).
 */
export function stripInternalMarkers(content: string): string {
  return content.replace(INTERNAL_MARKER_RE, "").trim();
}

export function parseUploadedFiles(content: string): FileInMessage[] {
  // Match <uploaded_files>...</uploaded_files> tag
  const uploadedFilesRegex = /<uploaded_files>([\s\S]*?)<\/uploaded_files>/;
  // eslint-disable-next-line @typescript-eslint/prefer-regexp-exec
  const match = content.match(uploadedFilesRegex);

  if (!match) {
    return [];
  }

  const uploadedFilesContent = match[1];

  // Check if it's "No files have been uploaded yet."
  if (uploadedFilesContent?.includes("No files have been uploaded yet.")) {
    return [];
  }

  // Check if the backend reported no new files were uploaded in this message
  if (uploadedFilesContent?.includes("(empty)")) {
    return [];
  }

  // Parse file list
  // Format: - filename (size)\n  Path: /path/to/file
  const fileRegex = /- ([^\n(]+)\s*\(([^)]+)\)\s*\n\s*Path:\s*([^\n]+)/g;
  const files: FileInMessage[] = [];
  let fileMatch;

  while ((fileMatch = fileRegex.exec(uploadedFilesContent ?? "")) !== null) {
    files.push({
      filename: fileMatch[1].trim(),
      size: parseInt(fileMatch[2].trim(), 10) ?? 0,
      path: fileMatch[3].trim(),
    });
  }

  return files;
}
