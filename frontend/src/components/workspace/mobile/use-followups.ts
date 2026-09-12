"use client";

import type { Message } from "@langchain/langgraph-sdk";
import { useCallback, useEffect, useRef, useState } from "react";

import { getBackendBaseURL } from "@/core/config";
import { isHiddenFromUIMessage } from "@/core/messages/utils";
import { useSuggestionsConfig } from "@/core/suggestions/hooks";
import { textOfMessage } from "@/core/threads/utils";

/** The desktop caps the response at five chips (`input-box.tsx`); so does this. */
export const FOLLOWUP_LIMIT = 5;
/** How much of the transcript is sent as context, same as the desktop. */
const RECENT_MESSAGE_LIMIT = 6;
/** How many the backend is asked for, same as the desktop. */
const REQUESTED_SUGGESTIONS = 3;

/**
 * Follow-up suggestions for the mobile composer (`FEATURE_LIST.md` C10).
 *
 * The desktop asks for these just after a run settles and paints them above the
 * input box; on mobile that component is not rendered at all, so the request
 * has to be issued here or the chips can never appear. The trigger, the payload
 * and the "one batch per assistant message" guard mirror `input-box.tsx`
 * (`messagesRef` / `lastGeneratedForAiIdRef`) so both trees ask the backend the
 * same question at the same moment.
 *
 * `isStreaming` is the composer's `status`, which is derived from the same
 * thread the desktop reads — the request fires on the streaming → idle edge.
 */
export function useFollowups({
  threadId,
  messages,
  isStreaming,
  disabled = false,
}: {
  threadId: string;
  messages: Message[];
  isStreaming: boolean;
  disabled?: boolean;
}) {
  const { data: suggestionsConfig } = useSuggestionsConfig();
  const configLoaded = suggestionsConfig !== undefined;
  const suggestionsEnabled = suggestionsConfig?.enabled === true;

  const [followups, setFollowups] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  // The transcript changes on every chunk; the effect must not re-run for it.
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const previousIsStreaming = useRef(isStreaming);
  const lastGeneratedForMessageId = useRef<string | null>(null);

  useEffect(() => {
    const wasStreaming = previousIsStreaming.current;
    previousIsStreaming.current = isStreaming;
    if (!wasStreaming || isStreaming || disabled) {
      return;
    }

    const current = messagesRef.current;
    const lastAssistant = [...current]
      .reverse()
      .find((message) => message.type === "ai");
    const lastAssistantId = lastAssistant?.id ?? null;
    if (
      !lastAssistantId ||
      lastAssistantId === lastGeneratedForMessageId.current
    ) {
      return;
    }
    if (!configLoaded) {
      return;
    }
    lastGeneratedForMessageId.current = lastAssistantId;

    const recent = current
      .filter((message) => message.type === "human" || message.type === "ai")
      .filter((message) => !isHiddenFromUIMessage(message))
      .map((message) => ({
        role: message.type === "human" ? "user" : "assistant",
        content: textOfMessage(message) ?? "",
      }))
      .filter((message) => message.content.trim().length > 0)
      .slice(-RECENT_MESSAGE_LIMIT);

    if (recent.length === 0) {
      return;
    }
    if (!suggestionsEnabled) {
      setFollowups([]);
      return;
    }

    const controller = new AbortController();
    setDismissed(false);
    setLoading(true);
    setFollowups([]);

    fetch(`${getBackendBaseURL()}/api/threads/${threadId}/suggestions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: recent, n: REQUESTED_SUGGESTIONS }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          return { suggestions: [] as string[] };
        }
        return (await response.json()) as { suggestions?: string[] };
      })
      .then((data) => {
        setFollowups(
          (data.suggestions ?? [])
            .map((suggestion) =>
              typeof suggestion === "string" ? suggestion.trim() : "",
            )
            .filter((suggestion) => suggestion.length > 0)
            .slice(0, FOLLOWUP_LIMIT),
        );
      })
      .catch(() => {
        setFollowups([]);
      })
      .finally(() => {
        setLoading(false);
      });

    return () => controller.abort();
  }, [configLoaded, disabled, isStreaming, suggestionsEnabled, threadId]);

  const dismiss = useCallback(() => {
    setDismissed(true);
    setFollowups([]);
  }, []);

  return {
    followups,
    loading,
    dismiss,
    visible: !disabled && !dismissed && (loading || followups.length > 0),
  };
}
