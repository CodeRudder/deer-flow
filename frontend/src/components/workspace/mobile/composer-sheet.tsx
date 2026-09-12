"use client";

import {
  BrainIcon,
  CameraIcon,
  CheckIcon,
  ChevronRightIcon,
  CpuIcon,
  GaugeIcon,
  GraduationCapIcon,
  ImageIcon,
  ImagesIcon,
  LightbulbIcon,
  ListChecksIcon,
  MessageSquareIcon,
  PaperclipIcon,
  RocketIcon,
  VideoIcon,
  ZapIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import {
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorInput,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorTrigger,
} from "@/components/ai-elements/model-selector";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ImageGenerationSelector } from "@/components/workspace/image-generation-selector";
import {
  ModelOptionContent,
  modelOptionRowClassName,
} from "@/components/workspace/model-menu";
import { VideoGenerationSelector } from "@/components/workspace/video-generation-selector";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { ModelProviderLogo } from "@/core/models/logo";
import { cn } from "@/lib/utils";

import type { ComposerContext } from "./composer";
import { resolveMode, type ComposerMode } from "./composer-mode";

/**
 * Every desktop control the mobile main row cannot hold ends up here
 * (prototype ③): the model picker (a header trigger on the desktop) plus the
 * whole footer row — mode, image model, video model, reasoning effort. The
 * main row keeps only ＋ / input / send.
 *
 * Three row shapes are used:
 * - the four "grab" cells are plain buttons (相册 / 拍照 / 文件 / 思考);
 * - 模型 opens the desktop's own model dialog, chat/vision toggle included;
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

const MODES: {
  mode: ComposerMode;
  labelKey: "flashMode" | "reasoningMode" | "proMode" | "ultraMode";
  descriptionKey:
    | "flashModeDescription"
    | "reasoningModeDescription"
    | "proModeDescription"
    | "ultraModeDescription";
  icon: typeof ZapIcon;
}[] = [
  {
    mode: "flash",
    labelKey: "flashMode",
    descriptionKey: "flashModeDescription",
    icon: ZapIcon,
  },
  {
    mode: "thinking",
    labelKey: "reasoningMode",
    descriptionKey: "reasoningModeDescription",
    icon: LightbulbIcon,
  },
  {
    mode: "pro",
    labelKey: "proMode",
    descriptionKey: "proModeDescription",
    icon: GraduationCapIcon,
  },
  {
    mode: "ultra",
    labelKey: "ultraMode",
    descriptionKey: "ultraModeDescription",
    icon: RocketIcon,
  },
];

const EFFORTS = [
  { effort: "minimal", labelKey: "reasoningEffortMinimal" },
  { effort: "low", labelKey: "reasoningEffortLow" },
  { effort: "medium", labelKey: "reasoningEffortMedium" },
  { effort: "high", labelKey: "reasoningEffortHigh" },
] as const;

type ExpandedSection = "mode" | "effort" | "plan" | null;

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
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const [modelCategory, setModelCategory] = useState<"chat" | "vision">("chat");
  // The same hook the composer already calls — identical query key, so this
  // costs no extra request. The row needs the list to label its trigger the
  // way the backend will resolve it.
  const { models, visionModels } = useModels();

  const mode = context.mode ?? (supportThinking ? "pro" : "flash");
  // Plan mode is not a separate setting: the backend derives it from the mode
  // (`is_plan_mode: mode === "pro" || mode === "ultra"`, see
  // core/threads/hooks.ts), so the switch shows and writes that derivation
  // instead of inventing a second source of truth.
  const planMode = mode === "pro" || mode === "ultra";
  const modeLabels = {
    flash: t.inputBox.flashMode,
    thinking: t.inputBox.reasoningMode,
    pro: t.inputBox.proMode,
    ultra: t.inputBox.ultraMode,
  } satisfies Record<ComposerMode, string>;
  const effort = context.reasoning_effort ?? "medium";
  const effortLabels = {
    minimal: t.inputBox.reasoningEffortMinimal,
    low: t.inputBox.reasoningEffortLow,
    medium: t.inputBox.reasoningEffortMedium,
    high: t.inputBox.reasoningEffortHigh,
  } satisfies Record<(typeof EFFORTS)[number]["effort"], string>;

  // Both fall back the way the desktop picker does, so the row's label is the
  // model the request will actually name: no pinned chat model → the first
  // configured one; no pinned vision model → the default one, then the first.
  const selectedModel =
    models.find((model) => model.name === context.model_name) ?? models[0];
  const selectedVisionModel =
    visionModels.find((model) => model.name === context.vision_model_name) ??
    visionModels.find((model) => model.is_default) ??
    visionModels[0];

  // An empty vision list must not leave the dialog stuck on a blank category.
  useEffect(() => {
    if (modelCategory === "vision" && visionModels.length === 0) {
      setModelCategory("chat");
    }
  }, [modelCategory, visionModels.length]);

  const selectModel = (model_name: string) => {
    const model = models.find((entry) => entry.name === model_name);
    if (!model) {
      return;
    }
    // Switching to a model that cannot think invalidates a thinking mode, so
    // it is re-resolved rather than carried over (same rule as the desktop).
    onContextChange({
      ...context,
      model_name,
      mode: resolveMode(context.mode, model.supports_thinking ?? false),
    });
    setModelDialogOpen(false);
  };

  const selectVisionModel = (vision_model_name: string) => {
    onContextChange({ ...context, vision_model_name });
    setModelDialogOpen(false);
  };

  const toggleSection = (section: Exclude<ExpandedSection, null>) => {
    setExpanded((current) => (current === section ? null : section));
  };

  const selectMode = (next: ComposerMode) => {
    // A model that cannot think has no non-Flash mode, so the selection is
    // coerced instead of silently ignored by the request builder.
    const resolved = resolveMode(next, supportThinking);
    onContextChange({
      ...context,
      mode: resolved,
      reasoning_effort:
        resolved === "ultra"
          ? "high"
          : resolved === "pro"
            ? "medium"
            : resolved === "thinking"
              ? "low"
              : "minimal",
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
          {/* The model row owns the whole picker rather than expanding in place
              like the rows below: chat and vision are two independent
              selections with a searchable list each, which does not fit a
              bottom sheet. The dialog is the desktop's own component, so the
              chat/vision toggle and the search box come with it. */}
          <ModelSelector
            open={modelDialogOpen}
            onOpenChange={setModelDialogOpen}
          >
            <ModelSelectorTrigger asChild>
              <button
                type="button"
                data-testid="mobile-composer-sheet-model"
                className={ROW_CLASS}
              >
                <CpuIcon
                  aria-hidden="true"
                  className="text-muted-foreground size-4 shrink-0"
                />
                <span className="min-w-0 flex-1 text-left">
                  {t.inputBox.model}
                </span>
                <span className="text-muted-foreground flex items-center gap-1 text-sm">
                  <ModelProviderLogo
                    className="size-4"
                    displayName={selectedModel?.display_name}
                    name={[selectedModel?.name, selectedModel?.model]
                      .filter(Boolean)
                      .join(" ")}
                  />
                  <span className="max-w-32 truncate">
                    {selectedModel?.display_name}
                  </span>
                  <ChevronRightIcon
                    aria-hidden="true"
                    className="size-4 shrink-0"
                  />
                </span>
              </button>
            </ModelSelectorTrigger>
            <ModelSelectorContent
              className="mobile-model-dialog"
              title={t.inputBox.model}
            >
              <ModelSelectorInput placeholder={t.inputBox.searchModels} />
              {visionModels.length > 0 && (
                <div className="border-b px-3 py-2">
                  <div className="bg-muted inline-grid w-full grid-cols-2 rounded-md p-1">
                    {(
                      [
                        {
                          id: "chat" as const,
                          label: t.inputBox.chatModel,
                          icon: MessageSquareIcon,
                        },
                        {
                          id: "vision" as const,
                          label: t.inputBox.visionModel,
                          icon: ImageIcon,
                        },
                      ] satisfies {
                        id: "chat" | "vision";
                        label: string;
                        icon: typeof ZapIcon;
                      }[]
                    ).map(({ id, label, icon: Icon }) => (
                      <button
                        key={id}
                        type="button"
                        data-testid={`mobile-composer-model-category-${id}`}
                        aria-pressed={modelCategory === id}
                        // `min-h-11`: the desktop's toggle is 36px, below the
                        // 44px touch floor.
                        className={cn(
                          "flex min-h-11 items-center justify-center gap-2 rounded-sm px-3 text-sm transition-colors",
                          modelCategory === id
                            ? "bg-background text-foreground shadow-sm"
                            : "text-muted-foreground",
                        )}
                        onClick={() => setModelCategory(id)}
                      >
                        <Icon aria-hidden="true" className="size-4 shrink-0" />
                        <span className="truncate">{label}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <ModelSelectorList className="max-h-80 p-1">
                {modelCategory === "chat"
                  ? models.map((model) => (
                      <ModelSelectorItem
                        key={model.name}
                        data-testid={`mobile-composer-model-${model.name}`}
                        className={modelOptionRowClassName(
                          model.name === selectedModel?.name,
                        )}
                        value={`chat:${model.name} ${model.display_name}`}
                        onSelect={() => selectModel(model.name)}
                      >
                        <ModelOptionContent
                          label={model.display_name}
                          logo={
                            <ModelProviderLogo
                              className="size-4"
                              displayName={model.display_name}
                              name={[model.name, model.model]
                                .filter(Boolean)
                                .join(" ")}
                            />
                          }
                          selected={model.name === selectedModel?.name}
                        />
                      </ModelSelectorItem>
                    ))
                  : visionModels.map((model) => (
                      <ModelSelectorItem
                        key={model.name}
                        data-testid={`mobile-composer-vision-model-${model.name}`}
                        className={modelOptionRowClassName(
                          model.name === selectedVisionModel?.name,
                        )}
                        value={`vision:${model.name} ${model.display_name ?? ""}`}
                        onSelect={() => selectVisionModel(model.name)}
                      >
                        <ModelOptionContent
                          label={model.display_name ?? model.name}
                          logo={
                            <ModelProviderLogo
                              className="size-4"
                              displayName={model.display_name}
                              name={[model.name, model.model]
                                .filter(Boolean)
                                .join(" ")}
                            />
                          }
                          selected={model.name === selectedVisionModel?.name}
                        />
                      </ModelSelectorItem>
                    ))}
              </ModelSelectorList>
            </ModelSelectorContent>
          </ModelSelector>

          <button
            type="button"
            data-testid="mobile-composer-sheet-mode"
            aria-expanded={expanded === "mode"}
            onClick={() => toggleSection("mode")}
            className={ROW_CLASS}
          >
            <ZapIcon
              aria-hidden="true"
              className="text-muted-foreground size-4 shrink-0"
            />
            <span className="min-w-0 flex-1 text-left">{t.inputBox.mode}</span>
            <span className="text-muted-foreground flex items-center gap-1 text-sm">
              {modeLabels[mode]}
              <ChevronRightIcon
                aria-hidden="true"
                className={cn(
                  "size-4 transition-transform",
                  expanded === "mode" && "rotate-90",
                )}
              />
            </span>
          </button>
          {expanded === "mode" && (
            <div className="flex flex-col" data-testid="mobile-composer-modes">
              {MODES.filter(
                (item) => item.mode !== "thinking" || supportThinking,
              ).map(({ mode: value, labelKey, descriptionKey, icon: Icon }) => (
                <button
                  key={value}
                  type="button"
                  data-testid={`mobile-composer-mode-${value}`}
                  aria-pressed={value === mode}
                  onClick={() => selectMode(value)}
                  className={cn(
                    "active:bg-accent flex min-h-12 w-full items-center gap-3 px-4 text-left",
                    value === mode && "text-accent-foreground",
                  )}
                >
                  <Icon
                    aria-hidden="true"
                    className={cn(
                      "size-4 shrink-0",
                      value === "ultra" && "text-[#dabb5e]",
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-base">
                      {t.inputBox[labelKey]}
                    </span>
                    <span className="text-muted-foreground block truncate text-xs">
                      {t.inputBox[descriptionKey]}
                    </span>
                  </span>
                  {value === mode && (
                    <CheckIcon aria-hidden="true" className="size-4 shrink-0" />
                  )}
                </button>
              ))}
            </div>
          )}

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
