"use client";

import { CheckIcon, type LucideIcon } from "lucide-react";
import { type ReactNode } from "react";

import {
  type PromptInputActionMenuContentProps,
  PromptInputActionMenuContent,
  PromptInputActionMenuTrigger,
} from "@/components/ai-elements/prompt-input";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

// Shared visual language for all model pickers (chat/vision dialog, image and
// video generation menus): one row height, one selection color, one
// check/unconfigured slot layout, one menu shell (dialog-matched radius and
// elevation) and one trigger active tone.

/** Item class for a model row: shared height + selection tone (+ dimmed when unconfigured). */
export function modelOptionRowClassName(selected: boolean, configured = true) {
  return cn(
    "h-9",
    selected ? "text-accent-foreground" : "text-muted-foreground/75",
    !configured && "opacity-50",
  );
}

type ModelOptionContentProps = {
  /** Leading logo; omit for logo-less rows (e.g. the "default" row). */
  logo?: ReactNode;
  label: ReactNode;
  selected: boolean;
  /** Unconfigured rows swap the check slot for a label and stay dimmed. */
  configured?: boolean;
  notConfiguredLabel?: ReactNode;
  /** Extra info placed before the check slot (e.g. video point rate). */
  meta?: ReactNode;
};

export function ModelOptionContent({
  logo,
  label,
  selected,
  configured = true,
  notConfiguredLabel,
  meta,
}: ModelOptionContentProps) {
  return (
    <>
      {logo}
      <span className="min-w-0 flex-1 truncate font-medium">{label}</span>
      <span className="flex shrink-0 items-center gap-2">
        {meta}
        {!configured ? (
          <span className="text-muted-foreground/60 text-xs">
            {notConfiguredLabel}
          </span>
        ) : selected ? (
          <CheckIcon className="size-4" />
        ) : (
          <span className="size-4" />
        )}
      </span>
    </>
  );
}

/** Trigger button for generation-model menus: icon plus the selected model name.
 * When selected, the caller-passed (borderless) model logo replaces the generic
 * icon; min-w-0 lets the button shrink so the toolbar row never wraps. */
export function ModelMenuTrigger({
  icon: Icon,
  label,
  selectedLabel,
  selectedLogo,
}: {
  icon: LucideIcon;
  label: string;
  selectedLabel?: string;
  selectedLogo?: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <PromptInputActionMenuTrigger
          aria-label={label}
          className="min-w-0 gap-1! px-2! data-[state=open]:bg-accent data-[state=open]:text-accent-foreground"
        >
          {selectedLabel && selectedLogo ? (
            selectedLogo
          ) : (
            <Icon className="size-3" />
          )}
          {selectedLabel && (
            <span className="hidden max-w-28 truncate text-xs font-normal sm:inline">
              {selectedLabel}
            </span>
          )}
        </PromptInputActionMenuTrigger>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/** Shared shell for generation-model menus: dialog-matched radius and elevation. */
export function ModelMenuContent({
  className,
  ...props
}: PromptInputActionMenuContentProps) {
  return (
    <PromptInputActionMenuContent
      className={cn("rounded-lg shadow-lg", className)}
      {...props}
    />
  );
}
