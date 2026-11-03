import type { Message } from "@langchain/langgraph-sdk";

import type { PendingInterjection } from "@/core/threads/interjection";
import { env } from "@/env";

import {
  extractContentFromMessage,
  extractReasoningContentFromMessage,
  findToolCallResult,
  hasContent,
  hasPresentFiles,
  hasSubagent,
  hasToolCalls,
  isClarificationToolMessage,
  isHiddenFromUIMessage,
  type MessageGroup,
} from "./utils";

/**
 * Process timeline normalizer (docs/plans/2026-11-03-process-timeline-design.md).
 *
 * Turns the raw transcript into a flat, monotonically ordered entry stream that
 * replaces the `assistant:processing` mega-group: every reasoning / tool call
 * becomes its own single-line `step` entry, content-only AI messages become
 * `answer` entries at their transcript position, and queued interjections are
 * placed at their injection point instead of the list tail.
 */

export function isProcessTimelineEnabled() {
  // Feature flag (D9): the legacy `MessageGroup` panel path stays in place for
  // one rollout cycle; this flag flips the transcript to the timeline renderer.
  return env.NEXT_PUBLIC_PROCESS_TIMELINE === "true";
}

export type TimelineStepStatus = "running" | "done" | "failed";

export interface TimelineStep {
  id: string;
  messageId?: string;
  variant: "reasoning" | "toolCall" | "narration";
  /** reasoning variant: full thinking text. */
  reasoning?: string | null;
  /** narration variant: intermediate assistant text kept at its position. */
  content?: string;
  /** toolCall variant. */
  name?: string;
  args?: Record<string, unknown>;
  /** Parsed tool result when the tool message content is valid JSON. */
  result?: unknown;
  /** Raw tool result text (used for sanitizer placeholder detection). */
  resultText?: string;
  /** `running` = no tool message yet; `failed` = tool message with error status (D7). */
  status: TimelineStepStatus;
  /** write_file / str_replace: row click opens the artifact panel. */
  opensArtifact?: boolean;
}

export type TimelineEntry =
  | {
      kind: "human";
      id: string;
      message: Message;
      /** D4: thin divider above the merged input that starts an auto-run. */
      autoRunDivider?: boolean;
    }
  | {
      kind: "interjection";
      id: string;
      item: PendingInterjection;
      /** Resolved text: the item's text, or the merged auto-run input. */
      text: string;
      /** True once the backend applied (injected) the interjection. */
      applied: boolean;
      /** The landed HumanMessage this event replaces at its injection spot. */
      landedMessage?: Message;
      autoRunDivider?: boolean;
    }
  | { kind: "step"; id: string; step: TimelineStep; roundIndex: number }
  | { kind: "answer"; id: string; message: Message; streaming: boolean }
  | { kind: "artifact"; id: string; group: MessageGroup };

export interface BuildTimelineEntriesOptions {
  isLoading?: boolean;
  interjections?: PendingInterjection[];
}

/** Tools whose row is a link into the artifact panel (same set as MessageGroup). */
const ARTIFACT_STEP_NAMES = new Set(["write_file", "str_replace"]);

function hasToolCallNamed(message: Message, toolName: string) {
  return (
    message.type === "ai" &&
    message.tool_calls?.some((toolCall) => toolCall.name === toolName) === true
  );
}

export function buildTimelineEntries(
  messages: Message[],
  { isLoading = false, interjections = [] }: BuildTimelineEntriesOptions = {},
): TimelineEntry[] {
  const entries: TimelineEntry[] = [];

  // Tool result statuses: first tool message per tool_call_id wins; a tool
  // message with error status marks its step failed (D7).
  const toolStatusById = new Map<string, TimelineStepStatus>();
  for (const message of messages) {
    if (message.type !== "tool" || !message.tool_call_id) {
      continue;
    }
    if (toolStatusById.has(message.tool_call_id)) {
      continue;
    }
    toolStatusById.set(
      message.tool_call_id,
      message.status === "error" ? "failed" : "done",
    );
  }

  // D4: the merged human input that spawns an auto-run gets a divider, both
  // before the bubble lands (interjection entry) and after (human entry).
  const autoRunInputTexts = new Set<string>();
  for (const item of interjections) {
    if (item.auto_run_run_id && item.auto_run_input) {
      autoRunInputTexts.add(item.auto_run_input);
    }
  }

  // Applied interjections are matched to their landed HumanMessage by text so
  // the event renders once, at the injection position, and the plain human
  // bubble is not duplicated beside it.
  const appliedByText = new Map<string, PendingInterjection>();
  for (const item of interjections) {
    if (item.applied && item.text) {
      appliedByText.set(item.text, item);
    }
    if (item.auto_run_run_id && item.auto_run_input) {
      appliedByText.set(item.auto_run_input, item);
    }
  }

  const humanTexts = new Set<string>();
  let roundCounter = 0;
  let currentTurnStartIndex = -1;
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index];
    if (message?.type === "human" && !isHiddenFromUIMessage(message)) {
      currentTurnStartIndex = index;
      break;
    }
  }

  for (const [messageIndex, message] of messages.entries()) {
    if (isHiddenFromUIMessage(message)) {
      continue;
    }

    if (message.type === "human") {
      const text = extractContentFromMessage(message);
      humanTexts.add(text);
      const matched = appliedByText.get(text);
      if (matched) {
        // Merge: the interjection event takes over this transcript slot (the
        // injection position); no separate human bubble is painted.
        entries.push({
          kind: "interjection",
          id: `interjection-${matched.id}`,
          item: matched,
          text: matched.applied && matched.text ? matched.text : text,
          applied: true,
          landedMessage: message,
          autoRunDivider: autoRunInputTexts.has(text),
        });
      } else {
        entries.push({
          kind: "human",
          id: message.id ?? `human-${messageIndex}`,
          message,
          autoRunDivider: autoRunInputTexts.has(text),
        });
      }
      roundCounter = 0;
      continue;
    }

    if (message.type === "tool") {
      if (isClarificationToolMessage(message)) {
        entries.push({
          kind: "artifact",
          id: message.id ?? `clarification-${messageIndex}`,
          group: {
            id: message.id,
            type: "assistant:clarification",
            messages: [message],
          },
        });
      }
      // Other tool results are folded into their tool-call step (lookup by id).
      continue;
    }

    if (message.type !== "ai") {
      continue;
    }

    if (hasPresentFiles(message)) {
      entries.push({
        kind: "artifact",
        id: message.id ?? `present-files-${messageIndex}`,
        group: {
          id: message.id,
          type: "assistant:present-files",
          messages: [message],
        },
      });
      continue;
    }

    if (hasSubagent(message)) {
      entries.push({
        kind: "artifact",
        id: message.id ?? `subagent-${messageIndex}`,
        group: {
          id: message.id,
          type: "assistant:subagent",
          messages: [message],
        },
      });
      continue;
    }

    const isClarificationMessage = hasToolCallNamed(
      message,
      "ask_clarification",
    );
    const content = extractContentFromMessage(message);
    // Streaming text may still gain tool-call chunks later (#4304): until the
    // turn settles it renders as a streaming answer block, ready to degrade
    // into steps on the next chunk.
    const isUnresolvedAssistantText =
      isLoading &&
      currentTurnStartIndex >= 0 &&
      messageIndex > currentTurnStartIndex &&
      hasContent(message) &&
      !hasToolCalls(message);
    // A content-only, settled AI message owns its reasoning inside the bubble
    // collapsible; painting it again as a step would render it twice (#3868).
    const becomesAssistantBubble =
      hasContent(message) &&
      !hasToolCalls(message) &&
      !isUnresolvedAssistantText;

    if (isUnresolvedAssistantText) {
      entries.push({
        kind: "answer",
        id: message.id ?? `answer-${messageIndex}`,
        message,
        streaming: true,
      });
      continue;
    }

    if (becomesAssistantBubble) {
      entries.push({
        kind: "answer",
        id: message.id ?? `answer-${messageIndex}`,
        message,
        streaming: false,
      });
      continue;
    }

    // Anything that reaches here is process content: emit its steps in place.
    const reasoning = extractReasoningContentFromMessage(message);
    const hasSteps = Boolean(reasoning) || hasToolCalls(message);
    if (!hasSteps && !(isClarificationMessage && content)) {
      continue;
    }
    if (hasToolCalls(message)) {
      roundCounter += 1;
    }
    const roundIndex = roundCounter;

    if (reasoning) {
      entries.push({
        kind: "step",
        id: message.id ?? `reasoning-${messageIndex}`,
        roundIndex,
        step: {
          id: message.id ?? `reasoning-${messageIndex}`,
          messageId: message.id,
          variant: "reasoning",
          reasoning,
          status: "done",
        },
      });
    }

    // Clarification question text renders in the bubble + card, not as a
    // narration row (same carve-out as the legacy panel).
    if (content && !isClarificationMessage && hasToolCalls(message)) {
      entries.push({
        kind: "step",
        id: `${message.id ?? `ai-${messageIndex}`}-content`,
        roundIndex,
        step: {
          id: `${message.id ?? `ai-${messageIndex}`}-content`,
          messageId: message.id,
          variant: "narration",
          content,
          status: "done",
        },
      });
    }

    for (const toolCall of message.tool_calls ?? []) {
      if (toolCall.name === "task" || toolCall.name === "ask_clarification") {
        continue;
      }
      const id =
        toolCall.id ?? `${message.id ?? `ai-${messageIndex}`}-${toolCall.name}`;
      const status = toolCall.id
        ? (toolStatusById.get(toolCall.id) ??
          (isLoading ? "running" : "failed"))
        : isLoading
          ? "running"
          : "failed";
      const resultText = toolCall.id
        ? (findToolCallResult(toolCall.id, messages) ?? undefined)
        : undefined;
      let result: unknown;
      if (resultText) {
        try {
          result = JSON.parse(resultText);
        } catch {
          result = resultText;
        }
      }
      entries.push({
        kind: "step",
        id,
        roundIndex,
        step: {
          id,
          messageId: message.id,
          variant: "toolCall",
          name: toolCall.name,
          args: toolCall.args,
          result,
          resultText,
          status,
          opensArtifact: ARTIFACT_STEP_NAMES.has(toolCall.name),
        },
      });
    }

    // A tool-calling clarification keeps its question bubble above the card.
    if (isClarificationMessage && content) {
      entries.push({
        kind: "answer",
        id: message.id ?? `answer-${messageIndex}`,
        message,
        streaming: false,
      });
    }
  }

  // Interjections: an applied bubble whose text has landed as a real
  // HumanMessage is suppressed — the landed human entry renders in its place
  // (HumanMessage-first priority, so the prune race never double-paints).
  // Everything else queues at the timeline tail, auto-run items behind a D4
  // divider.
  const landed = (item: PendingInterjection) =>
    (item.applied && humanTexts.has(item.text)) ||
    Boolean(
      item.auto_run_run_id &&
      item.auto_run_input != null &&
      humanTexts.has(item.auto_run_input),
    );

  for (const item of [...interjections].sort((a, b) => a.seq - b.seq)) {
    if (landed(item)) {
      continue;
    }
    entries.push({
      kind: "interjection",
      id: `interjection-${item.id}`,
      item,
      text:
        item.auto_run_run_id && item.auto_run_input
          ? item.auto_run_input
          : item.text,
      applied: Boolean(item.applied),
      autoRunDivider: Boolean(item.auto_run_run_id),
    });
  }

  return entries;
}
