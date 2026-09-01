import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { QuotaIndicator } from "@/components/workspace/quota-indicator";
import type { QuotaScopeItem } from "@/core/quotas/types";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      quotaIndicator: {
        label: "我的额度",
        prefixLabel: "额度",
        used: "已用",
        reserved: "预占",
        unitPoints: "积分",
        unitCount: "次",
        periodWeekly: "每周",
        periodMonthly: "每月",
        overrideNote: "管理员已调整本期额度",
        primaryNote: "默认展示已用比例最高的额度",
      },
    },
  }),
}));

const fetchQuotaMe = vi.hoisted(() => vi.fn());
vi.mock("@/core/quotas/api", () => ({ fetchQuotaMe }));

function videoItem(overrides: Partial<QuotaScopeItem> = {}): QuotaScopeItem {
  return {
    scope_id: "s-video",
    scope_code: "video_generation",
    scope_name: "视频资源",
    resource_type: "video_generation",
    period_type: "weekly",
    period: {
      period_type: "weekly",
      label: "2026-09-01~2026-09-07",
      period_start: "2026-09-01",
      period_end: "2026-09-07",
      timezone: "Asia/Shanghai",
    },
    status: "normal",
    source: "scope_default",
    requests: null,
    images: null,
    videos: {
      enforced: true,
      limit: 100,
      used: 12,
      reserved: 0,
      remaining: 88,
      ratio: 0.12,
      status: "normal",
      unit: "points",
    },
    ...overrides,
  };
}

function imageItem(overrides: Partial<QuotaScopeItem> = {}): QuotaScopeItem {
  return {
    scope_id: "s-image",
    scope_code: "image_generation",
    scope_name: "生图资源",
    resource_type: "image_generation",
    period_type: "monthly",
    period: {
      period_type: "monthly",
      label: "2026-09",
      period_start: "2026-09-01",
      period_end: "2026-09-30",
      timezone: "Asia/Shanghai",
    },
    status: "normal",
    source: "scope_default",
    requests: null,
    images: {
      enforced: true,
      limit: 5,
      used: 2,
      reserved: null,
      remaining: 3,
      ratio: 0.4,
      status: "normal",
      unit: "count",
    },
    videos: null,
    ...overrides,
  };
}

function modelItem(overrides: Partial<QuotaScopeItem> = {}): QuotaScopeItem {
  return {
    scope_id: "s-model",
    scope_code: "claude_model",
    scope_name: "高级模型",
    resource_type: "model",
    period_type: "weekly",
    period: {
      period_type: "weekly",
      label: "2026-09-01~2026-09-07",
      period_start: "2026-09-01",
      period_end: "2026-09-07",
      timezone: "Asia/Shanghai",
    },
    status: "normal",
    source: "scope_default",
    requests: {
      enforced: true,
      limit: 200,
      used: 12,
      reserved: null,
      remaining: 188,
      ratio: 0.06,
      status: "normal",
      unit: "count",
    },
    images: null,
    videos: null,
    ...overrides,
  };
}

function renderIndicator(items: QuotaScopeItem[], isThreadBusy = false) {
  fetchQuotaMe.mockResolvedValue({
    user_id: "user-1",
    status: "normal",
    items,
  });
  const client = new QueryClient();
  const invalidateSpy = vi.spyOn(client, "invalidateQueries");
  const view = render(
    <QueryClientProvider client={client}>
      <QuotaIndicator isThreadBusy={isThreadBusy} />
    </QueryClientProvider>,
  );
  return { ...view, invalidateSpy, client };
}

describe("QuotaIndicator", () => {
  it("renders the pill for configured dimensions", async () => {
    renderIndicator([videoItem()]);
    expect(await screen.findByLabelText("我的额度")).toBeTruthy();
    // 主显维度名 + 已用百分比（ratio 接口下发）
    expect(screen.getByText("视频资源")).toBeTruthy();
    expect(screen.getByText("12%")).toBeTruthy();
  });

  it("renders nothing when no dimensions are configured", async () => {
    const { container } = renderIndicator([]);
    await waitFor(() => expect(fetchQuotaMe).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a warning tone for exceeded status", async () => {
    const { container } = renderIndicator([
      videoItem({
        status: "exceeded",
        videos: {
          enforced: true,
          limit: 100,
          used: 100,
          reserved: 0,
          remaining: 0,
          ratio: 1,
          status: "exceeded",
          unit: "points",
        },
      }),
    ]);
    await screen.findByLabelText("我的额度");
    expect(container.querySelector(".text-red-600")).toBeTruthy();
  });

  it("renders used value for record-only dimension without limit", async () => {
    const { container } = renderIndicator([
      videoItem({
        status: "unlimited",
        videos: {
          enforced: false,
          limit: null,
          used: 5,
          reserved: null,
          remaining: null,
          ratio: null,
          status: "unlimited",
          unit: "points",
        },
      }),
    ]);
    await screen.findByLabelText("我的额度");
    // 仅记录态主显"已用"而非剩余
    expect(screen.getByText("5")).toBeTruthy();
    expect(container.querySelector(".text-red-600")).toBeNull();
  });

  it("prefers the more severe dimension as primary", async () => {
    renderIndicator([
      videoItem(),
      imageItem({
        status: "exceeded",
        images: {
          enforced: true,
          limit: 5,
          used: 5,
          reserved: null,
          remaining: 0,
          ratio: 1,
          status: "exceeded",
          unit: "count",
        },
      }),
    ]);
    await screen.findByLabelText("我的额度");
    // 生图已超额：主显生图 100%，而非视频 12%
    expect(screen.getByText("100%")).toBeTruthy();
    expect(screen.queryByText("12%")).toBeNull();
  });

  it("lists model dimensions in the dropdown with matching icons", async () => {
    renderIndicator([videoItem(), modelItem()]);
    await screen.findByLabelText("我的额度");
    // 已用比例最高优先：视频 12% > 高级模型 6%，主显视频
    expect(screen.getByText("12%")).toBeTruthy();

    // 模型组维度收在下拉中：名称未命中品牌时按 scope_code 命中——claude_model → Anthropic logo
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("我的额度"));
    expect(await screen.findByText("高级模型")).toBeTruthy();
    expect(document.querySelector("[style*='anthropic']")).toBeTruthy();
    expect(document.querySelector("[class*='lucide-video']")).toBeTruthy();
    expect(document.querySelector("[class*='lucide-image']")).toBeNull();
  });

  it("invalidates the quota cache when a thread run ends", async () => {
    const { invalidateSpy, client, rerender } = renderIndicator(
      [videoItem()],
      true,
    );
    await screen.findByLabelText("我的额度");
    expect(invalidateSpy).not.toHaveBeenCalled();

    rerender(
      <QueryClientProvider client={client}>
        <QuotaIndicator isThreadBusy={false} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["quotas", "me"] }),
    );
  });
});
