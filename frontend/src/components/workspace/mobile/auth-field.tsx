import { type ComponentProps } from "react";

import { cn } from "@/lib/utils";

/**
 * Text field for the mobile auth screens (prototype ⑧).
 *
 * The three mobile rules the plan calls out are encoded here once so no screen
 * can drift from them:
 *
 * - `text-base` (16px) — any smaller and iOS Safari zooms the whole page on
 *   focus, which then never zooms back out.
 * - `h-12` (48px) — above the 44px touch-target floor.
 * - `text-base` also overrides nothing from the shared `Input`, because this is
 *   a plain `<input>`: the desktop `Input` hardcodes `md:text-sm`, which is
 *   exactly the desktop-only behaviour we must not inherit on a phone.
 *
 * `type` is forwarded, so the caller keeps `type="email"` for the @-and-dot
 * keyboard.
 */
export function MobileAuthField({
  className,
  ...props
}: ComponentProps<"input">) {
  return (
    <input
      className={cn(
        "border-input bg-card text-foreground placeholder:text-muted-foreground h-12 w-full min-w-0 rounded-xl border px-3 text-base outline-none",
        "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
        "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

/** Label for a `MobileAuthField`; paired by id, hence the `htmlFor` passthrough. */
export function MobileAuthLabel({
  className,
  ...props
}: ComponentProps<"label">) {
  return (
    <label
      className={cn("mb-1.5 block text-[13px] font-medium", className)}
      {...props}
    />
  );
}
