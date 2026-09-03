"use client";

import { VideoIcon } from "lucide-react";
import { useMemo } from "react";

import {
  PromptInputActionMenu,
  PromptInputActionMenuItem,
} from "@/components/ai-elements/prompt-input";
import { DropdownMenuGroup } from "@/components/ui/dropdown-menu";
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

import {
  ModelMenuContent,
  ModelMenuTrigger,
  ModelOptionContent,
  modelOptionRowClassName,
} from "./model-menu";

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
  // No pinned model: mirror the backend default (first configured provider's
  // first model) so the menu and the trigger show the real default model.
  const defaultModel = useMemo(
    () => entries.find((entry) => entry.configured),
    [entries],
  );
  const effectiveModel = selectedModel ?? defaultModel;
  const isUnavailable = data?.skill_enabled === false;
  const triggerLabel =
    effectiveModel?.model.display_name ?? t.inputBox.videoGeneration;

  return (
    <PromptInputActionMenu>
      <ModelMenuTrigger
        icon={VideoIcon}
        label={t.inputBox.videoGeneration}
        selectedLabel={triggerLabel}
        iconClassName="text-violet-600 dark:text-violet-400"
      />
      <ModelMenuContent className="w-72">
        <DropdownMenuGroup>
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
              const isSelected = effectiveModel?.model.name === model.name;
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
                  className={modelOptionRowClassName(isSelected, configured)}
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
                  <ModelOptionContent
                    configured={configured}
                    label={model.display_name}
                    logo={
                      <ModelProviderLogo
                        className="size-4"
                        name={model.name}
                        displayName={model.display_name}
                      />
                    }
                    meta={
                      hasRate && rate ? (
                        <span className="text-muted-foreground text-[11px]">
                          {t.inputBox.videoRateFromLabel(
                            videoRateRangeLabel(rate.min, rate.min),
                          )}
                        </span>
                      ) : undefined
                    }
                    notConfiguredLabel={t.inputBox.videoGenerationNotConfigured}
                    selected={isSelected}
                  />
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
      </ModelMenuContent>
    </PromptInputActionMenu>
  );
}
