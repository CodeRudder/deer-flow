"use client";

import { Badge } from "@/components/ui/badge";

/**
 * The three states of a capability whitelist, spelled out.
 *
 * `skills` / `tool_groups` are three-valued, not two-valued, and the whole
 * point of the read-only section is that the difference survives to the screen
 * — the gallery card cannot tell `null` from `[]`:
 *
 * - `null` (the key is absent from the agent's config, so the API omits it) —
 *   *inherit all*: the agent sees every enabled skill / tool group;
 * - `[]` — *none*: an explicit empty whitelist, so the agent sees nothing;
 * - a non-empty list — a whitelist of exactly those entries.
 *
 * Pure and exported so the classification can be pinned without rendering.
 */
export type WhitelistState = "inherit" | "none" | "list";

export function whitelistState(
  values: readonly string[] | null | undefined,
): WhitelistState {
  if (values == null) {
    return "inherit";
  }
  return values.length === 0 ? "none" : "list";
}

/**
 * One capability whitelist with its state rendered as a badge.
 *
 * Same semantics and same copy as the desktop `WhitelistRow` in
 * `agent-edit-sheet.tsx`, reshaped for a phone: the desktop keeps it inside the
 * edit drawer, whose internals are not exported, so the rendering is restated
 * here rather than the desktop file being opened up. `data-whitelist-state`
 * carries the classification that the badges only express visually.
 */
export function MobileAgentWhitelistRow({
  label,
  values,
  inheritText,
  noneText,
  testId,
}: {
  label: string;
  values: readonly string[] | null | undefined;
  inheritText: string;
  noneText: string;
  testId: string;
}) {
  const state = whitelistState(values);

  return (
    <div className="min-w-0" data-testid={testId} data-whitelist-state={state}>
      <p className="text-sm font-medium">{label}</p>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {state === "inherit" ? (
          <Badge variant="secondary">{inheritText}</Badge>
        ) : state === "none" ? (
          <Badge variant="outline">{noneText}</Badge>
        ) : (
          // `?? []` only satisfies the type checker: the list arm is reached
          // exactly when the value is a non-empty array.
          (values ?? []).map((value) => (
            <Badge
              key={value}
              variant="outline"
              className="max-w-full"
              title={value}
            >
              <span className="truncate">{value}</span>
            </Badge>
          ))
        )}
      </div>
    </div>
  );
}
