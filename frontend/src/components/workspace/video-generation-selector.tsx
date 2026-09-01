"use client";

import { CheckIcon, VideoIcon } from "lucide-react";
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
import { ModelProviderLogo } from "@/core/models/logo";
import { useVideoGenerationProviders } from "@/core/video-generation";
import {
  videoRatePerSecond,
  videoRateRangeLabel,
} from "@/core/video-generation/rate";
import { cn } from "@/lib/utils";

type VideoGenerationSelection = {
  video_generation_model?: string;
};

export function VideoGenerationSelector({
  selection,
  onSelectionChange,
}: {
  selection: VideoGenerationSelection;
  onSelectionChange: (selection: VideoGenerationSelection) => void;
}) {
  const { t } = useI18n();
  const { data, providers, isLoading, error } = useVideoGenerationProviders();

  // Flatten provider -> models into a single-level list of selectable entries,
  // so the menu shows each model (e.g. "MiniMax H3") directly with no submenu.
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
        (entry) => entry.model.name === selection.video_generation_model,
      ),
    [entries, selection.video_generation_model],
  );
  const hasSelection = Boolean(selection.video_generation_model);
  const isUnavailable = data?.skill_enabled === false;
  const triggerLabel =
    selectedModel?.model.display_name ?? t.inputBox.videoGenerationDefault;

  return (
    <PromptInputActionMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <PromptInputActionMenuTrigger
            aria-label={t.inputBox.videoGeneration}
            className={cn("gap-1! px-2!", hasSelection && "text-[#2aa7c9]")}
          >
            <VideoIcon
              className={cn("size-3", hasSelection && "text-[#2aa7c9]")}
            />
            {hasSelection && (
              <span className="max-w-28 truncate text-xs font-normal text-[#2aa7c9]">
                {triggerLabel}
              </span>
            )}
          </PromptInputActionMenuTrigger>
        </TooltipTrigger>
        <TooltipContent>{t.inputBox.videoGeneration}</TooltipContent>
      </Tooltip>
      <PromptInputActionMenuContent className="w-72">
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
                video_generation_model: undefined,
              })
            }
          >
            <span className="min-w-0 truncate font-medium">
              {t.inputBox.videoGenerationDefault}
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
              {t.inputBox.videoGenerationSkillDisabled}
            </PromptInputActionMenuItem>
          )}
          {!isLoading && error && (
            <PromptInputActionMenuItem disabled>
              {t.inputBox.videoGenerationLoadFailed}
            </PromptInputActionMenuItem>
          )}
          {!isLoading &&
            !error &&
            !isUnavailable &&
            entries.map(({ provider, model, configured }) => {
              const isSelected =
                selection.video_generation_model === model.name;
              const rate = model.billing
                ? videoRatePerSecond(model.billing)
                : null;
              const hasRate = Boolean(configured && rate);

              const hasDetail = Boolean(model.description ?? model.billing);

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
                      video_generation_model: model.name,
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
                  {hasRate && (
                    <span className="text-muted-foreground ml-auto shrink-0 text-[11px]">
                      {t.inputBox.videoRateFromLabel(
                        videoRateRangeLabel(rate!.min, rate!.min),
                      )}
                    </span>
                  )}
                  {!configured ? (
                    <span className="text-muted-foreground/60 ml-auto shrink-0 text-xs">
                      {t.inputBox.videoGenerationNotConfigured}
                    </span>
                  ) : isSelected ? (
                    <CheckIcon
                      className={cn("size-4", !hasRate && "ml-auto")}
                    />
                  ) : (
                    <div className={cn("size-4", !hasRate && "ml-auto")} />
                  )}
                </PromptInputActionMenuItem>
              );

              return hasDetail ? (
                <Tooltip key={`${provider.name}:${model.name}`}>
                  <TooltipTrigger asChild>{item}</TooltipTrigger>
                  <TooltipContent
                    side="right"
                    align="start"
                    className="max-w-72 leading-relaxed whitespace-normal"
                  >
                    {model.description}
                    {model.billing && (
                      <div className="text-muted-foreground mt-1.5 border-t pt-1.5">
                        {model.billing.resolutions.map((rate) => (
                          <div key={rate.resolution}>
                            {rate.resolution}：
                            {videoRateRangeLabel(
                              rate.yuan_per_second_min,
                              rate.yuan_per_second_max,
                            )}{" "}
                            {t.inputBox.videoPointsPerSecond}
                          </div>
                        ))}
                        {model.billing.regeneration_yuan_per_second != null && (
                          <div>
                            {t.inputBox.videoRegenerationLabel}：
                            {videoRateRangeLabel(
                              model.billing.regeneration_yuan_per_second,
                              model.billing.regeneration_yuan_per_second,
                            )}{" "}
                            {t.inputBox.videoPointsPerSecond}
                          </div>
                        )}
                        {model.billing.min_duration_seconds != null && (
                          <div>
                            {t.inputBox.videoDurationRange}
                            {model.billing.min_duration_seconds}
                            {model.billing.max_duration_seconds != null
                              ? `~${model.billing.max_duration_seconds}`
                              : ""}
                            {t.inputBox.videoDurationSeconds}
                          </div>
                        )}
                      </div>
                    )}
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
