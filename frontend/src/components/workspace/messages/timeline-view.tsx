"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { TimelineEntry } from "@/core/messages/timeline";
import { cn } from "@/lib/utils";

import { ProcessStepRow } from "./process-step-row";

/**
 * Renders the normalized timeline entry stream (design doc §2/§4).
 *
 * Non-step entries (human / answer / artifact / interjection) are delegated to
 * `renderEntry` — `MessageList` owns their rendering so the bubble, card and
 * file-list shapes stay byte-identical to the legacy path. This component owns
 * the step rows, their expansion set (container-level so streaming re-renders
 * never lose it) and the D2 auto-expand of the newest reasoning row.
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
  // D2: while streaming, the newest reasoning row auto-expands; when the run
  // settles every reasoning row collapses again (stable rows stay collapsed).
  const latestReasoningStepId = useMemo(() => {
    if (!isLoading) {
      return null;
    }
    for (let index = entries.length - 1; index >= 0; index--) {
      const entry = entries[index];
      if (entry?.kind === "step" && entry.step.variant === "reasoning") {
        return entry.step.id;
      }
    }
    return null;
  }, [entries, isLoading]);
  const autoExpandedIdsRef = useRef<ReadonlySet<string>>(new Set());
  const [autoExpandedIds, setAutoExpandedIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );

  useEffect(() => {
    const next = new Set(autoExpandedIdsRef.current);
    if (latestReasoningStepId) {
      next.add(latestReasoningStepId);
    } else {
      next.clear();
    }
    autoExpandedIdsRef.current = next;
    setAutoExpandedIds(next);
  }, [latestReasoningStepId]);

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
              forceExpanded={
                entry.step.variant === "reasoning" &&
                autoExpandedIds.has(entry.step.id)
              }
              failed={entry.step.status === "failed"}
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
