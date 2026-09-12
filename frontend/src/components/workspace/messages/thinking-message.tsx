"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";

import type { ReasoningTriggerProps } from "@/components/ai-elements/reasoning";
import { Shimmer } from "@/components/ai-elements/shimmer";
import type { Translations } from "@/core/i18n";
import { useI18n } from "@/core/i18n/hooks";

/**
 * Localized stand-in for `ReasoningTrigger`'s hardcoded English trigger
 * wording ("Thinking...", "Thought for 12s").
 *
 * `ai-elements/**` is registry-generated and must not be hand-edited, but
 * `ReasoningTrigger` takes an injectable `getThinkingMessage`, so the wording
 * comes from the call sites instead. This is not a mobile-only concern — the
 * English is wrong on the desktop too — so the shared `MessageList` /
 * `MessageListItem` call sites pass it (DEVELOPMENT_PLAN.md §1.3 exception).
 */

type ToolCallStrings = Translations["toolCalls"];

/**
 * Mirrors `formatDuration` in `ai-elements/reasoning.tsx` (same clamping, same
 * switch to minutes at 60s) but takes the unit wording from the locale — the
 * registry version always renders "12s" / "1m 5s".
 */
export function formatThinkingDuration(
  strings: ToolCallStrings,
  seconds: number,
): string {
  const normalized = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(normalized / 60);
  const remainingSeconds = normalized % 60;
  return minutes > 0
    ? strings.durationMinutes(minutes, remainingSeconds)
    : strings.durationSeconds(normalized);
}

/**
 * Localized `LiveTimer` from `ai-elements/reasoning.tsx`: identical markup and
 * the same one-second tick, only the two text nodes differ. The tick has to
 * survive here — swapping it for static wording would regress both trees.
 */
const LiveThinkingMessage = ({
  startTime,
  strings,
}: {
  startTime: number;
  strings: ToolCallStrings;
}) => {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const calculateElapsed = () => Math.floor((Date.now() - startTime) / 1000);
    setElapsed(calculateElapsed());

    const interval = setInterval(() => {
      setElapsed(calculateElapsed());
    }, 1000);

    return () => clearInterval(interval);
  }, [startTime]);

  return (
    <span className="flex items-center gap-2">
      <Shimmer duration={1}>{strings.thinking}</Shimmer>
      <span className="text-muted-foreground/80">
        ({formatThinkingDuration(strings, elapsed)})
      </span>
    </span>
  );
};

/**
 * Builds a `getThinkingMessage` with the same three branches as the registry's
 * `defaultGetThinkingMessage`: ticking while streaming with a start time,
 * "thought for N", or the no-duration fallback.
 */
export function createGetThinkingMessage(
  strings: ToolCallStrings,
): NonNullable<ReasoningTriggerProps["getThinkingMessage"]> {
  // Named so `react/display-name` sees a definition, not an anonymous one.
  function getThinkingMessage(
    isStreaming: boolean,
    duration?: number,
    startTime?: number | null,
  ): ReactNode {
    const normalizedDuration =
      typeof duration === "number" && Number.isFinite(duration)
        ? Math.max(0, Math.floor(duration))
        : undefined;

    if (isStreaming && startTime != null) {
      return <LiveThinkingMessage startTime={startTime} strings={strings} />;
    }
    if (isStreaming) {
      return <Shimmer duration={1}>{strings.thinking}</Shimmer>;
    }
    if (normalizedDuration === undefined) {
      return <span>{strings.thoughtBriefly}</span>;
    }
    return (
      <span>
        {strings.thoughtFor(
          formatThinkingDuration(strings, normalizedDuration),
        )}
      </span>
    );
  }

  return getThinkingMessage;
}

/**
 * `ReasoningTrigger` is memoized and compares props by identity, so hand it a
 * callback that only changes when the locale does.
 */
export function useGetThinkingMessage() {
  const { t } = useI18n();
  return useMemo(() => createGetThinkingMessage(t.toolCalls), [t]);
}
