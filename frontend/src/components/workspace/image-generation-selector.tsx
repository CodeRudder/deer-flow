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
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import { useI18n } from "@/core/i18n/hooks";
import { useImageGenerationProviders } from "@/core/image-generation";
import { cn } from "@/lib/utils";

type ImageGenerationSelection = {
  image_generation_provider?: string;
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

  const selectedProvider = useMemo(
    () =>
      providers.find(
        (provider) => provider.name === selection.image_generation_provider,
      ),
    [providers, selection.image_generation_provider],
  );
  const hasSelection = Boolean(
    selection.image_generation_provider && selection.image_generation_model,
  );
  const isUnavailable = data?.skill_enabled === false;
  const triggerLabel =
    selectedProvider?.display_name ?? t.inputBox.imageGenerationDefault;

  return (
    <PromptInputActionMenu>
      <PromptInputActionMenuTrigger
        aria-label={t.inputBox.imageGeneration}
        className={cn("gap-1! px-2!", hasSelection && "text-[#2aa7c9]")}
      >
        <ImageIcon className={cn("size-3", hasSelection && "text-[#2aa7c9]")} />
        {hasSelection && (
          <span className="max-w-28 truncate text-xs font-normal text-[#2aa7c9]">
            {triggerLabel}
          </span>
        )}
      </PromptInputActionMenuTrigger>
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
                image_generation_provider: undefined,
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
            providers.map((provider) => {
              const providerSelected =
                selection.image_generation_provider === provider.name;

              return (
                <DropdownMenuSub key={provider.name}>
                  <DropdownMenuSubTrigger
                    className={cn(
                      "h-9",
                      providerSelected
                        ? "text-accent-foreground"
                        : "text-muted-foreground/75",
                    )}
                  >
                    <span className="min-w-0 truncate font-medium">
                      {provider.display_name}
                    </span>
                  </DropdownMenuSubTrigger>
                  <DropdownMenuSubContent className="w-56">
                    {!provider.configured ? (
                      <PromptInputActionMenuItem
                        disabled
                        className="h-9 text-muted-foreground/70"
                      >
                        <span className="min-w-0 truncate">
                          {t.inputBox.imageGenerationNotConfigured}
                        </span>
                      </PromptInputActionMenuItem>
                    ) : (
                      provider.models.map((model) => {
                        const isSelected =
                          selection.image_generation_provider ===
                            provider.name &&
                          selection.image_generation_model === model.name;

                        return (
                          <PromptInputActionMenuItem
                            key={`${provider.name}:${model.name}`}
                            className={cn(
                              "h-9 pl-4",
                              isSelected
                                ? "text-accent-foreground"
                                : "text-muted-foreground/75",
                            )}
                            onSelect={() =>
                              onSelectionChange({
                                image_generation_provider: provider.name,
                                image_generation_model: model.name,
                              })
                            }
                          >
                            <span className="min-w-0 truncate font-medium">
                              {model.display_name}
                            </span>
                            {isSelected ? (
                              <CheckIcon className="ml-auto size-4" />
                            ) : (
                              <div className="ml-auto size-4" />
                            )}
                          </PromptInputActionMenuItem>
                        );
                      })
                    )}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              );
            })}
        </DropdownMenuGroup>
      </PromptInputActionMenuContent>
    </PromptInputActionMenu>
  );
}
