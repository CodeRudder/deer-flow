"use client";

import {
  BotIcon,
  ChevronDownIcon,
  ImageIcon,
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

/** 胶囊警示色取响应顶层 status（后端已按全维度取最严状态）。 */
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

function QuotaItemRow({ item }: { item: QuotaScopeItem }) {
  const { t } = useI18n();
  const metric = metricOf(item);
  if (!metric) return null;

  const isVideo = item.resource_type === "video_generation";
  const unitLabel =
    metric.unit === "points"
      ? t.quotaIndicator.unitPoints
      : t.quotaIndicator.unitCount;
  // 仅限额维度展示 / limit；不限额只显示实际用量
  const limit =
    metric.enforced && metric.limit != null
      ? `/${formatPoints(metric.limit)}`
      : "";
  const reserved = isVideo ? (metric.reserved ?? 0) : 0;
  const periodLabel =
    item.period_type === "monthly"
      ? t.quotaIndicator.periodMonthly
      : t.quotaIndicator.periodWeekly;

  return (
    <div className="space-y-0.5">
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
        <div className="text-muted-foreground flex min-w-0 items-center gap-1.5">
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
          <span className="text-foreground truncate">{item.scope_name}</span>
        </div>
        <span className="text-muted-foreground whitespace-nowrap">
          <span className="font-mono">
            {formatPoints(metric.used)}
            {limit}
          </span>{" "}
          {unitLabel}
          {reserved > 0
            ? ` (${t.quotaIndicator.reserved} ${formatPoints(reserved)})`
            : null}
        </span>
        <span
          className="text-muted-foreground/70 justify-self-end text-[10px]"
          title={`${periodLabel} ${item.period.label}`}
        >
          {periodLabel}
        </span>
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

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          aria-label={t.quotaIndicator.label}
          className={cn(
            "text-muted-foreground bg-background/70 hover:bg-background/90 flex h-auto items-center gap-1.5 rounded-full border px-2 py-1 text-xs font-normal",
            PILL_TONES[data.status] ?? "",
            className,
          )}
        >
          <WalletIcon size={14} />
          <span className="hidden sm:inline">
            {t.quotaIndicator.prefixLabel}
          </span>
          <ChevronDownIcon className="size-3" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="bottom" align="end" className="w-80">
        <DropdownMenuLabel>{t.quotaIndicator.label}</DropdownMenuLabel>
        <div className="space-y-2.5 px-2 py-1 text-xs">
          {data.items.map((item) => (
            <QuotaItemRow key={item.scope_id} item={item} />
          ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
