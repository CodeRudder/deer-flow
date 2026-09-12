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
