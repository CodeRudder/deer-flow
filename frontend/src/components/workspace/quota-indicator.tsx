"use client";

import {
  BotIcon,
  ChevronDownIcon,
  ImageIcon,
  TriangleAlertIcon,
  VideoIcon,
  WalletIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Progress } from "@/components/ui/progress";
import { useI18n } from "@/core/i18n/hooks";
import { ModelProviderLogo } from "@/core/models/logo";
import { formatPoints } from "@/core/quotas/format";
import { useInvalidateQuotaOnRunEnd, useQuotaMe } from "@/core/quotas/hooks";
import type { QuotaMetric, QuotaScopeItem } from "@/core/quotas/types";
import { cn } from "@/lib/utils";

type QuotaIndicatorProps = {
  /** thread 流是否运行中；运行结束的边沿触发余额刷新。 */
  isThreadBusy: boolean;
  className?: string;
};

const PILL_TONES: Record<string, string> = {
  warning: "text-amber-600 dark:text-amber-400",
  exceeded: "text-red-600 dark:text-rose-400",
};

function metricOf(item: QuotaScopeItem): QuotaMetric | null {
  return item.requests ?? item.videos ?? item.images;
}

/** 维度图标按 resource_type 匹配（模型组/生图/视频）。 */
const DIMENSION_ICONS = {
  model: BotIcon,
  image_generation: ImageIcon,
  video_generation: VideoIcon,
} as const;

function DimensionIcon({
  resourceType,
  className,
}: {
  resourceType: string;
  className?: string;
}) {
  const Icon =
    DIMENSION_ICONS[resourceType as keyof typeof DIMENSION_ICONS] ?? BotIcon;
  return <Icon className={className} />;
}

/** 主显维度：已用比例最高优先（剩余比例最少；同级视频优先，全部无比例时视频优先）。 */
function pickPrimaryItem(items: QuotaScopeItem[]): QuotaScopeItem | null {
  const withRatio = items.filter((item) => metricOf(item)?.ratio != null);
  const candidates = withRatio.length > 0 ? withRatio : items;
  return (
    [...candidates].sort((a, b) => {
      const ratioGap = (metricOf(b)?.ratio ?? 0) - (metricOf(a)?.ratio ?? 0);
      if (ratioGap !== 0) return ratioGap;
      return (
        (a.resource_type === "video_generation" ? 0 : 1) -
        (b.resource_type === "video_generation" ? 0 : 1)
      );
    })[0] ?? null
  );
}

function QuotaItemRow({ item }: { item: QuotaScopeItem }) {
  const { t } = useI18n();
  const metric = metricOf(item);
  if (!metric) return null;

  const isVideo = item.resource_type === "video_generation";
  const unitLabel =
    metric.unit === "points"
      ? t.quotaIndicator.unitPoints
      : t.quotaIndicator.unitCount;
  const limit = metric.limit;
  const reserved = isVideo ? (metric.reserved ?? 0) : 0;

  return (
    <div className="space-y-1">
      <div className="text-muted-foreground flex items-center gap-1.5">
        {item.resource_type === "model" ? (
          // 固定映射：claude_model 用 Anthropic 品牌 logo，其余模型组走组件兜底
          <ModelProviderLogo
            name={item.scope_code === "claude_model" ? "claude" : ""}
            className="size-4"
          />
        ) : (
          <DimensionIcon
            resourceType={item.resource_type}
            className="size-3.5"
          />
        )}
        <span>
          <span className="text-foreground">{item.scope_name}</span>
          {" - "}
          {t.quotaIndicator.used} {formatPoints(metric.used)}
          {limit != null ? ` / ${formatPoints(limit)}` : ""} {unitLabel}
        </span>
      </div>
      {reserved > 0 && (
        <div className="text-muted-foreground">
          {t.quotaIndicator.reserved} {formatPoints(reserved)} {unitLabel}
        </div>
      )}
      {metric.ratio != null && (
        <Progress className="h-1.5" value={metric.ratio * 100} />
      )}
      <div className="text-muted-foreground flex items-center justify-between gap-4">
        <span>
          {item.period_type === "monthly"
            ? t.quotaIndicator.periodMonthly
            : t.quotaIndicator.periodWeekly}
        </span>
        <span>{item.period.label}</span>
      </div>
      {item.source === "temporary_override" && (
        <div className="text-muted-foreground">
          {t.quotaIndicator.overrideNote}
        </div>
      )}
    </div>
  );
}

export function QuotaIndicator({
  isThreadBusy,
  className,
}: QuotaIndicatorProps) {
  const { t } = useI18n();
  useInvalidateQuotaOnRunEnd(isThreadBusy);
  const { data, isPending, isError } = useQuotaMe();

  if (isPending || isError || !data || data.items.length === 0) {
    return null;
  }

  const primary = pickPrimaryItem(data.items);
  if (!primary) return null;
  const primaryMetric = metricOf(primary);
  if (!primaryMetric) return null;

  const unitLabel =
    primaryMetric.unit === "points"
      ? t.quotaIndicator.unitPoints
      : t.quotaIndicator.unitCount;
  const hasLimit = primaryMetric.limit != null;
  const pillTone = PILL_TONES[primaryMetric.status] ?? "";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          aria-label={t.quotaIndicator.label}
          className={cn(
            "text-muted-foreground bg-background/70 hover:bg-background/90 flex h-auto items-center gap-1.5 rounded-full border px-2 py-1 text-xs font-normal",
            pillTone,
            className,
          )}
        >
          <WalletIcon size={14} />
          <span className="hidden sm:inline">
            {t.quotaIndicator.prefixLabel}：
          </span>
          <span className="hidden sm:inline">{primary.scope_name}</span>
          {primaryMetric.status === "exceeded" && (
            <TriangleAlertIcon className="size-3.5" />
          )}
          <span className="font-mono">
            {hasLimit
              ? `${Math.round((primaryMetric.ratio ?? 0) * 100)}%`
              : formatPoints(primaryMetric.used)}
          </span>
          {!hasLimit && <span className="hidden sm:inline">{unitLabel}</span>}
          <ChevronDownIcon className="size-3" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="bottom" align="end" className="w-80">
        <DropdownMenuLabel>{t.quotaIndicator.label}</DropdownMenuLabel>
        <div className="space-y-3 px-2 py-1 text-xs">
          {data.items.map((item) => (
            <QuotaItemRow key={item.scope_id} item={item} />
          ))}
          <div className="text-muted-foreground/70 text-[11px]">
            {t.quotaIndicator.primaryNote}
          </div>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
