"use client";

import { CheckIcon, ImageIcon } from "lucide-react";
import { useMemo } from "react";

import {
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
} from "@/components/ai-elements/prompt-input";
import {
  DropdownMenuGroup,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useI18n } from "@/core/i18n/hooks";
import { useImageGenerationProviders } from "@/core/image-generation";
import { ModelProviderLogo } from "@/core/models/logo";
import { cn } from "@/lib/utils";

type ImageGenerationSelection = {
  image_generation_model?: string;
};

export function ImageGenerationSelector({
  selection,
  onSelectionChange,
}: {
  selection: ImageGenerationSelection;
  onSelectionChange: (selection: ImageGenerationSelection) => void;
}) {
  const { t } = useI18n();
  const { data, providers, isLoading, error } = useImageGenerationProviders();

  // Flatten provider -> models into a single-level list of selectable entries,
  // so the menu shows each model directly with no submenu. Provider is display
  // metadata only (grouping source, configured status); the selection sent
  // outwards carries the model name alone.
  const entries = useMemo(
    () =>
      providers.flatMap((provider) =>
        provider.models.map((model) => ({
          provider,
          model,
          configured: provider.configured,
        })),
      ),
    [providers],
  );

  const selectedModel = useMemo(
    () =>
      entries.find(
        (entry) => entry.model.name === selection.image_generation_model,
      ),
    [entries, selection.image_generation_model],
  );
  const hasSelection = Boolean(selection.image_generation_model);
  const isUnavailable = data?.skill_enabled === false;
  const triggerLabel =
    selectedModel?.model.display_name ?? t.inputBox.imageGenerationDefault;

  return (
    <PromptInputActionMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <PromptInputActionMenuTrigger
            aria-label={t.inputBox.imageGeneration}
            className={cn("gap-1! px-2!", hasSelection && "text-[#2aa7c9]")}
          >
            <ImageIcon
              className={cn("size-3", hasSelection && "text-[#2aa7c9]")}
            />
            {hasSelection && (
              <span className="max-w-28 truncate text-xs font-normal text-[#2aa7c9]">
                {triggerLabel}
              </span>
            )}
          </PromptInputActionMenuTrigger>
        </TooltipTrigger>
        <TooltipContent>{t.inputBox.imageGeneration}</TooltipContent>
      </Tooltip>
      <PromptInputActionMenuContent className="w-56">
        <DropdownMenuGroup>
          <PromptInputActionMenuItem
            className={cn(
              "h-9",
              !hasSelection
                ? "text-accent-foreground"
                : "text-muted-foreground/65",
            )}
            onSelect={() =>
              onSelectionChange({
                image_generation_model: undefined,
              })
            }
          >
            <span className="min-w-0 truncate font-medium">
              {t.inputBox.imageGenerationDefault}
            </span>
            {!hasSelection ? (
              <CheckIcon className="ml-auto size-4" />
            ) : (
              <div className="ml-auto size-4" />
            )}
          </PromptInputActionMenuItem>
          <DropdownMenuSeparator />
          {isLoading && (
            <PromptInputActionMenuItem disabled>
              {t.common.loading}
            </PromptInputActionMenuItem>
          )}
          {isUnavailable && (
            <PromptInputActionMenuItem disabled>
              {t.inputBox.imageGenerationSkillDisabled}
            </PromptInputActionMenuItem>
          )}
          {!isLoading && error && (
            <PromptInputActionMenuItem disabled>
              {t.inputBox.imageGenerationLoadFailed}
            </PromptInputActionMenuItem>
          )}
          {!isLoading &&
            !error &&
            !isUnavailable &&
            entries.map(({ provider, model, configured }) => {
              const isSelected =
                selection.image_generation_model === model.name;

              const item = (
                <PromptInputActionMenuItem
                  // Not `disabled`: Radix drops disabled items from roving focus and
                  // data-disabled kills pointer events, leaving the description
                  // tooltip unreachable. aria-disabled keeps hover/focus; onSelect blocks selection.
                  aria-disabled={!configured || undefined}
                  className={cn(
                    "h-9",
                    !configured && "opacity-50",
                    isSelected
                      ? "text-accent-foreground"
                      : "text-muted-foreground/75",
                  )}
                  onSelect={(event) => {
                    if (!configured) {
                      event.preventDefault();
                      return;
                    }
                    onSelectionChange({
                      image_generation_model: model.name,
                    });
                  }}
                >
                  <ModelProviderLogo
                    name={model.name}
                    displayName={model.display_name}
                  />
                  <span className="min-w-0 truncate font-medium">
                    {model.display_name}
                  </span>
                  {!configured ? (
                    <span className="text-muted-foreground/60 ml-auto shrink-0 text-xs">
                      {t.inputBox.imageGenerationNotConfigured}
                    </span>
                  ) : isSelected ? (
                    <CheckIcon className="ml-auto size-4" />
                  ) : (
                    <div className="ml-auto size-4" />
                  )}
                </PromptInputActionMenuItem>
              );

              return model.description ? (
                <Tooltip key={`${provider.name}:${model.name}`}>
                  <TooltipTrigger asChild>{item}</TooltipTrigger>
                  <TooltipContent
                    side="right"
                    align="start"
                    className="max-w-72 leading-relaxed whitespace-normal"
                  >
                    {model.description}
                  </TooltipContent>
                </Tooltip>
              ) : (
                <div key={`${provider.name}:${model.name}`}>{item}</div>
              );
            })}
        </DropdownMenuGroup>
      </PromptInputActionMenuContent>
    </PromptInputActionMenu>
  );
}
