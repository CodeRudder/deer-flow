/**
 * The mobile composer's mode vocabulary, shared by the composer and its `＋`
 * sheet.
 *
 * It lives in its own module because both need it and the two import each
 * other: `composer.tsx` renders `ComposerSheet`, and the sheet's 模型 row has
 * to re-resolve the mode against the model the user just picked. Putting the
 * rule in either component would close a runtime import cycle.
 */

export type ComposerMode = "flash" | "thinking" | "pro" | "ultra";

/**
 * Mirrors `input-box.tsx`'s private `getResolvedMode`: a model that cannot
 * think has no non-Flash mode, so every other selection collapses to Flash
 * instead of being silently dropped by the request builder.
 */
export function resolveMode(
  mode: ComposerMode | undefined,
  supportsThinking: boolean,
): ComposerMode {
  if (!supportsThinking && mode !== "flash") {
    return "flash";
  }
  return mode ?? (supportsThinking ? "pro" : "flash");
}

/** The reasoning-effort level each mode implies, mirroring the desktop menu. */
export type ComposerEffort = "minimal" | "low" | "medium" | "high";

const MODE_EFFORT: Record<ComposerMode, ComposerEffort> = {
  flash: "minimal",
  thinking: "low",
  pro: "medium",
  ultra: "high",
};

/**
 * The context patch a mode selection writes.
 *
 * One rule, three call sites — the mode picker, the `＋` panel's 思考 shortcut
 * and its 计划模式 row all write the same pair. Kept pure so the derivation is
 * unit-testable and cannot drift between them; the desktop writes exactly these
 * values in `input-box.tsx`'s `handleModeSelect`.
 */
export function modeSelection(
  mode: ComposerMode,
  supportsThinking: boolean,
): { mode: ComposerMode; reasoning_effort: ComposerEffort } {
  const resolved = resolveMode(mode, supportsThinking);
  return { mode: resolved, reasoning_effort: MODE_EFFORT[resolved] };
}
