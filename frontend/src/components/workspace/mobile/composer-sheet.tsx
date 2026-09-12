"use client";

import {
  BrainIcon,
  CameraIcon,
  CheckIcon,
  ChevronRightIcon,
  GaugeIcon,
  ImageIcon,
  ImagesIcon,
  ListChecksIcon,
  PaperclipIcon,
  VideoIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ImageGenerationSelector } from "@/components/workspace/image-generation-selector";
import { VideoGenerationSelector } from "@/components/workspace/video-generation-selector";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import type { ComposerContext } from "./composer";
import { modeSelection, resolveMode, type ComposerMode } from "./composer-mode";

/**
 * Every desktop control the mobile main row cannot hold ends up here
 * (prototype ③): the four "grab" cells (相册 / 拍照 / 文件 / 思考) plus the
 * image model, video model, reasoning effort and plan-mode rows.
 *
 * Mode and model are **not** here any more (T16): they are the two pills on the
 * composer's main row, and their layers live in `composer-pickers.tsx`.
 *
 * Two row shapes are used:
 * - the four "grab" cells are plain buttons;
 * - the remaining controls are plain rows, except the image and video menus,
 *   which reuse the desktop selectors verbatim — they keep their own trigger
 *   and dropdown, and only their size is overridden to a 48px row via
 *   descendant rules, the same technique the chat header uses.
 */

/** Row height shared by every list row: above the 44px touch floor. */
const ROW_CLASS = "flex min-h-12 w-full items-center gap-3 px-4 text-base";

/**
 * Re-sizes the reused model selectors' trigger. Their trigger is built for the
 * desktop toolbar (32px tall, label hidden below `sm`), so the row restores the
 * label and stretches the button; arbitrary variants out-specify the single
 * utility classes the shared components apply.
 */
const SELECTOR_ROW_CLASS =
  "[&_button]:min-h-12 [&_button]:min-w-11 [&_button]:gap-2 [&_button]:px-2 [&_span]:inline [&_span]:max-w-40 [&_span]:truncate [&_span]:text-sm";

const EFFORTS = [
  { effort: "minimal", labelKey: "reasoningEffortMinimal" },
  { effort: "low", labelKey: "reasoningEffortLow" },
  { effort: "medium", labelKey: "reasoningEffortMedium" },
  { effort: "high", labelKey: "reasoningEffortHigh" },
] as const;

type ExpandedSection = "effort" | "plan" | null;

type ComposerSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  context: ComposerContext;
  onContextChange: (context: ComposerContext) => void;
  /** Selected model cannot think: the mode menu hides 思考 and coerces to Flash. */
  supportThinking: boolean;
  supportReasoningEffort: boolean;
  onPickPhotoLibrary: () => void;
  onPickCamera: () => void;
  onPickFile: () => void;
};

/**
 * The `＋` panel (prototype ③).
 *
 * Sub-menus expand in place rather than opening a second layer: the sheet
 * already sits at the bottom of the screen, and a nested dropdown on top of a
 * bottom sheet is both easy to mis-tap and hard to position on a phone.
 */
export function ComposerSheet({
  open,
  onOpenChange,
  context,
  onContextChange,
  supportThinking,
  supportReasoningEffort,
  onPickPhotoLibrary,
  onPickCamera,
  onPickFile,
}: ComposerSheetProps) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState<ExpandedSection>(null);

  const mode = resolveMode(context.mode, supportThinking);
  // Plan mode is not a separate setting: the backend derives it from the mode
  // (`is_plan_mode: mode === "pro" || mode === "ultra"`, see
  // core/threads/hooks.ts), so the switch shows and writes that derivation
  // instead of inventing a second source of truth.
  const planMode = mode === "pro" || mode === "ultra";
  const effort = context.reasoning_effort ?? "medium";
  const effortLabels = {
    minimal: t.inputBox.reasoningEffortMinimal,
    low: t.inputBox.reasoningEffortLow,
    medium: t.inputBox.reasoningEffortMedium,
    high: t.inputBox.reasoningEffortHigh,
  } satisfies Record<(typeof EFFORTS)[number]["effort"], string>;

  const toggleSection = (section: Exclude<ExpandedSection, null>) => {
    setExpanded((current) => (current === section ? null : section));
  };

  const selectMode = (next: ComposerMode) => {
    // A model that cannot think has no non-Flash mode, so the selection is
    // coerced instead of silently ignored by the request builder. The
    // mode→effort pair comes from the shared rule, so this shortcut and the
    // mode pill cannot drift apart.
    onContextChange({
      ...context,
      ...modeSelection(next, supportThinking),
    });
    setExpanded(null);
  };

  const grabCell = (
    key: string,
    label: string,
    icon: ReactNode,
    onClick: () => void,
    pressed?: boolean,
  ) => (
    <button
      key={key}
      type="button"
      data-testid={`mobile-composer-sheet-${key}`}
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        "active:bg-accent flex min-h-16 flex-col items-center justify-center gap-1 rounded-xl px-1 py-2 text-[13px]",
        pressed && "bg-accent text-accent-foreground",
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        data-testid="mobile-composer-sheet"
        // `mobile-chat-sheet` re-sizes the shared close button (see
        // `chat-surface.css`); the composer owns the bottom inset itself
        // because it sits at the sheet's bottom edge.
        className="mobile-chat-sheet gap-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)]"
      >
        <SheetHeader>
          <SheetTitle className="text-base">
            {t.inputBox.composerOptions}
          </SheetTitle>
        </SheetHeader>

        <div className="grid grid-cols-4 gap-1 px-3">
          {grabCell(
            "gallery",
            t.inputBox.photoLibrary,
            <ImagesIcon aria-hidden="true" className="size-5" />,
            onPickPhotoLibrary,
          )}
          {grabCell(
            "camera",
            t.inputBox.takePhoto,
            <CameraIcon aria-hidden="true" className="size-5" />,
            onPickCamera,
          )}
          {grabCell(
            "file",
            t.inputBox.file,
            <PaperclipIcon aria-hidden="true" className="size-5" />,
            onPickFile,
          )}
          {/* Thinking is a one-tap shortcut for the mode below: it is on for
              every mode except Flash, which is exactly what the backend calls
              `thinking_enabled`. */}
          {grabCell(
            "thinking",
            t.inputBox.reasoningMode,
            <BrainIcon aria-hidden="true" className="size-5" />,
            () => selectMode(mode === "flash" ? "thinking" : "flash"),
            mode !== "flash",
          )}
        </div>

        <div className="flex flex-col pb-1">
          {/* Image / video generation keep the desktop selectors: the row only
              restores the label they hide below `sm` and stretches the trigger. */}
          <div
            data-testid="mobile-composer-sheet-image"
            className={cn(ROW_CLASS, "pr-2", SELECTOR_ROW_CLASS)}
          >
            <ImageIcon
              aria-hidden="true"
              className="text-muted-foreground size-4 shrink-0"
            />
            <span className="min-w-0 flex-1 text-left">
              {t.inputBox.imageGeneration}
            </span>
            <ImageGenerationSelector
              selection={{
                image_generation_model: context.image_generation_model,
              }}
              onSelectionChange={(selection) =>
                onContextChange({ ...context, ...selection })
              }
            />
          </div>

          <div
            data-testid="mobile-composer-sheet-video"
            className={cn(ROW_CLASS, "pr-2", SELECTOR_ROW_CLASS)}
          >
            <VideoIcon
              aria-hidden="true"
              className="text-muted-foreground size-4 shrink-0"
            />
            <span className="min-w-0 flex-1 text-left">
              {t.inputBox.videoGeneration}
            </span>
            <VideoGenerationSelector
              selection={{
                video_generation_model: context.video_generation_model,
              }}
              onSelectionChange={(selection) =>
                onContextChange({ ...context, ...selection })
              }
            />
          </div>

          {supportReasoningEffort && mode !== "flash" && (
            <>
              <button
                type="button"
                data-testid="mobile-composer-sheet-effort"
                aria-expanded={expanded === "effort"}
                onClick={() => toggleSection("effort")}
                className={ROW_CLASS}
              >
                <GaugeIcon
                  aria-hidden="true"
                  className="text-muted-foreground size-4 shrink-0"
                />
                <span className="min-w-0 flex-1 text-left">
                  {t.inputBox.reasoningEffort}
                </span>
                <span className="text-muted-foreground flex items-center gap-1 text-sm">
                  {effortLabels[effort]}
                  <ChevronRightIcon
                    aria-hidden="true"
                    className={cn(
                      "size-4 transition-transform",
                      expanded === "effort" && "rotate-90",
                    )}
                  />
                </span>
              </button>
              {expanded === "effort" && (
                <div
                  className="flex flex-col"
                  data-testid="mobile-composer-efforts"
                >
                  {EFFORTS.map(({ effort: value, labelKey }) => (
                    <button
                      key={value}
                      type="button"
                      data-testid={`mobile-composer-effort-${value}`}
                      aria-pressed={value === effort}
                      onClick={() => {
                        onContextChange({
                          ...context,
                          reasoning_effort: value,
                        });
                        setExpanded(null);
                      }}
                      className={cn(
                        "active:bg-accent flex min-h-12 w-full items-center gap-3 px-4 text-left text-base",
                        value === effort && "text-accent-foreground",
                      )}
                    >
                      <span className="min-w-0 flex-1">
                        {t.inputBox[labelKey]}
                      </span>
                      {value === effort && (
                        <CheckIcon
                          aria-hidden="true"
                          className="size-4 shrink-0"
                        />
                      )}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}

          <button
            type="button"
            data-testid="mobile-composer-sheet-plan"
            aria-expanded={expanded === "plan"}
            onClick={() => toggleSection("plan")}
            className={ROW_CLASS}
          >
            <ListChecksIcon
              aria-hidden="true"
              className="text-muted-foreground size-4 shrink-0"
            />
            <span className="min-w-0 flex-1 text-left">
              {t.inputBox.planMode}
            </span>
            <span className="text-muted-foreground flex items-center gap-1 text-sm">
              {planMode ? t.inputBox.planModeOn : t.inputBox.planModeOff}
              <ChevronRightIcon
                aria-hidden="true"
                className={cn(
                  "size-4 transition-transform",
                  expanded === "plan" && "rotate-90",
                )}
              />
            </span>
          </button>
          {expanded === "plan" && (
            <div className="flex flex-col" data-testid="mobile-composer-plans">
              {(
                [
                  { value: false, labelKey: "planModeOff" },
                  { value: true, labelKey: "planModeOn" },
                ] as const
              ).map(({ value, labelKey }) => (
                <button
                  key={labelKey}
                  type="button"
                  data-testid={`mobile-composer-plan-${value ? "on" : "off"}`}
                  aria-pressed={value === planMode}
                  onClick={() => selectMode(value ? "pro" : "thinking")}
                  className={cn(
                    "active:bg-accent flex min-h-12 w-full items-center gap-3 px-4 text-left text-base",
                    value === planMode && "text-accent-foreground",
                  )}
                >
                  <span className="min-w-0 flex-1">{t.inputBox[labelKey]}</span>
                  {value === planMode && (
                    <CheckIcon aria-hidden="true" className="size-4 shrink-0" />
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
