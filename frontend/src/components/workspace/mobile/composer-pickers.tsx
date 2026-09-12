"use client";

import {
  CheckIcon,
  GraduationCapIcon,
  ImageIcon,
  LightbulbIcon,
  MessageSquareIcon,
  RocketIcon,
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
import {
  ModelOptionContent,
  modelOptionRowClassName,
} from "@/components/workspace/model-menu";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { ModelProviderLogo } from "@/core/models/logo";
import { cn } from "@/lib/utils";

import type { ComposerContext } from "./composer";
import { modeSelection, resolveMode, type ComposerMode } from "./composer-mode";

/**
 * The two selection layers the composer's mode/model pills open (T16).
 *
 * Both were rows inside the `＋` panel until the pills moved to the main row.
 * They live here rather than in `composer-sheet.tsx` because the sheet no
 * longer renders them: the pill is the only entry point now, and importing
 * them back from the sheet would make the sheet a pass-through.
 *
 * Interaction shape follows the plan: the pill is a read-only label whose tap
 * opens a layer — a bottom `Sheet` for the four modes, the desktop's own
 * `ModelSelector` dialog for the models (chat/vision tabs and search included).
 * Nothing is edited inline in the main row, so tapping a pill can never raise
 * the keyboard or steal focus from the textarea.
 */

/** Each mode's glyph, shared by the list below and the composer's mode pill. */
export const MODE_ICONS: Record<ComposerMode, typeof ZapIcon> = {
  flash: ZapIcon,
  thinking: LightbulbIcon,
  pro: GraduationCapIcon,
  ultra: RocketIcon,
};

const MODES: {
  mode: ComposerMode;
  labelKey: "flashMode" | "reasoningMode" | "proMode" | "ultraMode";
  descriptionKey:
    | "flashModeDescription"
    | "reasoningModeDescription"
    | "proModeDescription"
    | "ultraModeDescription";
}[] = [
  {
    mode: "flash",
    labelKey: "flashMode",
    descriptionKey: "flashModeDescription",
  },
  {
    mode: "thinking",
    labelKey: "reasoningMode",
    descriptionKey: "reasoningModeDescription",
  },
  {
    mode: "pro",
    labelKey: "proMode",
    descriptionKey: "proModeDescription",
  },
  {
    mode: "ultra",
    labelKey: "ultraMode",
    descriptionKey: "ultraModeDescription",
  },
];

type ComposerModeSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  context: ComposerContext;
  onContextChange: (context: ComposerContext) => void;
  /** Selected model cannot think: 思考 is hidden and every mode coerces to Flash. */
  supportThinking: boolean;
};

/** The mode sub-menu, opened by the mode pill (prototype ② `⚡ 思考 ▾`). */
export function ComposerModeSheet({
  open,
  onOpenChange,
  context,
  onContextChange,
  supportThinking,
}: ComposerModeSheetProps) {
  const { t } = useI18n();
  const mode = resolveMode(context.mode, supportThinking);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        data-testid="mobile-composer-mode-sheet"
        className="mobile-chat-sheet gap-2 pb-[calc(env(safe-area-inset-bottom)+0.75rem)]"
      >
        <SheetHeader>
          <SheetTitle className="text-base">{t.inputBox.mode}</SheetTitle>
        </SheetHeader>
        <div className="flex flex-col pb-1" data-testid="mobile-composer-modes">
          {MODES.filter(
            (item) => item.mode !== "thinking" || supportThinking,
          ).map(({ mode: value, labelKey, descriptionKey }) => {
            const Icon = MODE_ICONS[value];
            return (
              <button
                key={value}
                type="button"
                data-testid={`mobile-composer-mode-${value}`}
                aria-pressed={value === mode}
                onClick={() => {
                  onContextChange({
                    ...context,
                    ...modeSelection(value, supportThinking),
                  });
                  onOpenChange(false);
                }}
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
            );
          })}
        </div>
      </SheetContent>
    </Sheet>
  );
}

type ComposerModelPickerProps = {
  context: ComposerContext;
  onContextChange: (context: ComposerContext) => void;
  /** The pill in the main row; `ModelSelectorTrigger` clones it. */
  trigger: ReactNode;
};

/**
 * The model picker, opened by the model pill (prototype ② `⚙ GLM-5.3 Flash ▾`).
 *
 * The dialog is the desktop's own component, so the chat/vision toggle and the
 * search box come with it — the same thing the `＋` panel's 模型 row opened.
 */
export function ComposerModelPicker({
  context,
  onContextChange,
  trigger,
}: ComposerModelPickerProps) {
  const { t } = useI18n();
  const { models, visionModels } = useModels();
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<"chat" | "vision">("chat");

  // Both fall back the way the desktop picker does, so the list marks the model
  // the request will actually name: no pinned chat model → the first configured
  // one; no pinned vision model → the default one, then the first.
  const selectedModel =
    models.find((model) => model.name === context.model_name) ?? models[0];
  const selectedVisionModel =
    visionModels.find((model) => model.name === context.vision_model_name) ??
    visionModels.find((model) => model.is_default) ??
    visionModels[0];

  // An empty vision list must not leave the dialog stuck on a blank category.
  useEffect(() => {
    if (category === "vision" && visionModels.length === 0) {
      setCategory("chat");
    }
  }, [category, visionModels.length]);

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
    setOpen(false);
  };

  const selectVisionModel = (vision_model_name: string) => {
    onContextChange({ ...context, vision_model_name });
    setOpen(false);
  };

  return (
    <ModelSelector open={open} onOpenChange={setOpen}>
      <ModelSelectorTrigger asChild>{trigger}</ModelSelectorTrigger>
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
                  aria-pressed={category === id}
                  // `min-h-11`: the desktop's toggle is 36px, below the 44px
                  // touch floor.
                  className={cn(
                    "flex min-h-11 items-center justify-center gap-2 rounded-sm px-3 text-sm transition-colors",
                    category === id
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground",
                  )}
                  onClick={() => setCategory(id)}
                >
                  <Icon aria-hidden="true" className="size-4 shrink-0" />
                  <span className="truncate">{label}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        <ModelSelectorList className="max-h-80 p-1">
          {category === "chat"
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
  );
}
