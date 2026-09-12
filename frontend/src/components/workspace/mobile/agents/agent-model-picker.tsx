"use client";

import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { useState } from "react";

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { MODEL_DEFAULT_VALUE } from "@/components/workspace/agents/agent-edit-sheet";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import "./agents-surface.css";

/**
 * The model list a form offers, with the agent's own model kept in it.
 *
 * Both forms let the user pick any configured model, but an agent may be pinned
 * to a model that is no longer in `config.yaml` — dropping it would silently
 * rewrite the agent's model on the next save. Same rule as the desktop edit
 * sheet (`agent-edit-sheet.tsx`), which unshifts it for the same reason; pure
 * so it can be pinned without rendering.
 */
export function modelOptionsWithCurrent(
  models: string[],
  current: string | null,
): string[] {
  if (current && current !== MODEL_DEFAULT_VALUE && !models.includes(current)) {
    return [current, ...models];
  }
  return models;
}

/**
 * Model field for the mobile create/edit screens.
 *
 * A button that shows the current choice and opens a bottom sheet of options —
 * the shape `composer-pickers.tsx` uses for the composer's model pill, rather
 * than the desktop's anchored `Select`, which is a 32px-row dropdown and below
 * the touch floor. The rows here are 48px and selected state is a check, so
 * nothing depends on hover.
 *
 * `MODEL_DEFAULT_VALUE` is the shared sentinel: picking "follow default model"
 * stores the sentinel locally and both screens map it to `model: null` on the
 * wire, exactly like the desktop sheets.
 */
export function MobileAgentModelPicker({
  id,
  value,
  options,
  onChange,
  disabled,
}: {
  id: string;
  value: string;
  options: string[];
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);

  const choices = [MODEL_DEFAULT_VALUE, ...options];

  const labelOf = (option: string) =>
    option === MODEL_DEFAULT_VALUE ? t.agents.modelDefault : option;

  return (
    <>
      <button
        type="button"
        id={id}
        data-testid="mobile-agent-model-trigger"
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className={cn(
          "border-input bg-card flex min-h-12 w-full min-w-0 items-center gap-2 rounded-xl border px-3 text-left text-base",
          "focus-visible:border-ring focus-visible:ring-ring/50 outline-none focus-visible:ring-[3px]",
          "disabled:pointer-events-none disabled:opacity-50",
        )}
      >
        <span className="min-w-0 flex-1 truncate">{labelOf(value)}</span>
        <ChevronDownIcon
          aria-hidden="true"
          className="text-muted-foreground size-4 shrink-0"
        />
      </button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          side="bottom"
          data-testid="mobile-agent-model-sheet"
          className="mobile-agents-sheet gap-2 pb-[calc(env(safe-area-inset-bottom)+0.75rem)]"
        >
          <SheetHeader>
            <SheetTitle className="text-base">{t.agents.modelLabel}</SheetTitle>
          </SheetHeader>
          {/* Wrapped so the sheet's `> button` close-button rule (see
              `agents-surface.css`) cannot reach the option rows. */}
          <div className="flex max-h-[60vh] flex-col overflow-y-auto pb-1">
            {choices.map((option) => {
              const selected = option === value;
              return (
                <button
                  key={option}
                  type="button"
                  data-testid={
                    option === MODEL_DEFAULT_VALUE
                      ? "mobile-agent-model-default"
                      : `mobile-agent-model-${option}`
                  }
                  aria-pressed={selected}
                  onClick={() => {
                    onChange(option);
                    setOpen(false);
                  }}
                  className={cn(
                    "active:bg-accent flex min-h-12 w-full min-w-0 items-center gap-3 px-4 text-left",
                    selected && "text-accent-foreground",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate text-base">
                    {labelOf(option)}
                  </span>
                  {selected && (
                    <CheckIcon aria-hidden="true" className="size-4 shrink-0" />
                  )}
                </button>
              );
            })}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
