"use client";

import type { Message } from "@langchain/langgraph-sdk";
import { useCallback, useMemo } from "react";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";
import {
  buildHumanInputResponseText,
  hasOpenHumanInputRequest,
  type HumanInputRequest,
  type HumanInputResponse,
} from "@/core/messages/human-input";
import { isHiddenFromUIMessage } from "@/core/messages/utils";

type SendMessageFn = (
  threadId: string,
  message: PromptInputMessage,
  extraContext?: Record<string, unknown>,
  options?: { additionalKwargs?: Record<string, unknown> },
) => Promise<void>;

/**
 * Clarification-card wiring shared by both chat pages. Strict mode: while a
 * card awaits an answer the composer is disabled and regenerate is blocked.
 * The answer is sent as a hidden HumanMessage (`hide_from_ui` +
 * `human_input_response`) that the journal/transcript filters keep so the
 * answered state survives reload.
 */
export function useHumanInput({
  threadId,
  sendMessage,
  messages,
  extraContext,
  enabled = true,
}: {
  threadId: string;
  sendMessage: SendMessageFn;
  messages: Message[];
  /** Stream context, e.g. `{ agent_name }` on agent chats. */
  extraContext?: Record<string, unknown>;
  /** Gate, e.g. false in mock/static modes. */
  enabled?: boolean;
}): {
  hasOpenHumanInputCard: boolean;
  handleSubmitHumanInput: (
    request: HumanInputRequest,
    response: HumanInputResponse,
  ) => Promise<boolean>;
} {
  const hasOpenHumanInputCard = useMemo(
    () =>
      enabled &&
      hasOpenHumanInputRequest(
        messages,
        (message) => !isHiddenFromUIMessage(message),
      ),
    [enabled, messages],
  );

  const handleSubmitHumanInput = useCallback(
    async (request: HumanInputRequest, response: HumanInputResponse) => {
      await sendMessage(
        threadId,
        {
          text: buildHumanInputResponseText(request, response),
          files: [],
        },
        extraContext,
        {
          additionalKwargs: {
            hide_from_ui: true,
            human_input_response: response,
          },
        },
      );
      return true;
    },
    [extraContext, sendMessage, threadId],
  );

  return { hasOpenHumanInputCard, handleSubmitHumanInput };
}
