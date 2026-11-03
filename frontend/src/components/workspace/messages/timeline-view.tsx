"use client";

import { useMemo } from "react";

import type { TimelineEntry } from "@/core/messages/timeline";
import { cn } from "@/lib/utils";

import { ProcessStepRow } from "./process-step-row";

/**
 * Renders the normalized timeline entry stream (design doc §2/§4).
 *
 * Non-step entries (human / answer / artifact / interjection) are delegated to
 * `renderEntry` — `MessageList` owns their rendering so the bubble, card and
 * file-list shapes stay byte-identical to the legacy path. This component owns
 * the step rows and the streaming-tail pointer (req 4: the last in-progress
 * row's collapsed preview follows the stream). Nothing auto-expands (D2
 * revised by user testing): details are manual click only.
 */

export function TimelineView({
  entries,
  isLoading,
  renderEntry,
  className,
}: {
  entries: TimelineEntry[];
  isLoading: boolean;
  renderEntry: (entry: TimelineEntry) => React.ReactNode;
  className?: string;
}) {
  // D2 (revised by user testing): nothing auto-expands — every step row stays
  // collapsed and compact; details are manual click only (expanded behaviour
  // unchanged: formatted content, 10-line clamp, view-more).

  // Req 4 (scope widened): while streaming, the LAST step row — any variant,
  // the newest reasoning included — scrolls its collapsed preview with the
  // stream (tail lines); every settled row keeps its head-lines preview fixed.
  const streamingTailStepId = useMemo(() => {
    if (!isLoading) {
      return null;
    }
    for (let index = entries.length - 1; index >= 0; index--) {
      const entry = entries[index];
      if (entry?.kind === "step") {
        return entry.step.id;
      }
    }
    return null;
  }, [entries, isLoading]);

  return (
    <div className={cn("flex w-full flex-col gap-0.5", className)}>
      {entries.map((entry, index) => {
        if (entry.kind !== "step") {
          return (
            <div key={entry.id} className="w-full">
              {entry.kind === "interjection" && entry.autoRunDivider && (
                <AutoRunDivider />
              )}
              {renderEntry(entry)}
            </div>
          );
        }
        return (
          <div key={`step-wrapper-${entry.id}-${index}`} className="w-full">
            <ProcessStepRow
              step={entry.step}
              roundIndex={entry.roundIndex}
              isLoading={isLoading}
              failed={entry.step.status === "failed"}
              streamingTail={entry.step.id === streamingTailStepId}
            />
          </div>
        );
      })}
    </div>
  );
}

export function AutoRunDivider() {
  return (
    <div
      className="text-muted-foreground/60 my-2 flex w-full items-center gap-2 text-xs"
      data-testid="auto-run-divider"
    >
      <span className="bg-border h-px flex-1" />
      <span className="shrink-0">→</span>
      <span className="bg-border h-px flex-1" />
    </div>
  );
}

// D7: a failed step opens with its error detail by default (`failed` prop) and
// remains expandable/collapsible by click.
export type { TimelineEntry };
