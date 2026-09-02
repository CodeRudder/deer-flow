import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { QuotaIndicator } from "@/components/workspace/quota-indicator";
import type { QuotaScopeItem } from "@/core/quotas/types";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      quotaIndicator: {
        label: "我的额度",
        prefixLabel: "额度",
        reserved: "预占",
        unitPoints: "积分",
        unitCount: "次",
        periodWeekly: "每周",
        periodMonthly: "每月",
        overrideNote: "管理员已调整本期额度",
      },
    },
  }),
}));

const fetchQuotaMe = rs.hoisted(() => rs.fn());
rs.mock("@/core/quotas/api", () => ({ fetchQuotaMe }));

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

function renderIndicator(
  items: QuotaScopeItem[],
  isThreadBusy = false,
  status = "normal",
) {
  fetchQuotaMe.mockResolvedValue({
    user_id: "user-1",
    status,
    items,
  });
  const client = new QueryClient();
  const invalidateSpy = rs.spyOn(client, "invalidateQueries");
  const view = render(
    <QueryClientProvider client={client}>
      <QuotaIndicator isThreadBusy={isThreadBusy} />
    </QueryClientProvider>,
  );
  return { ...view, invalidateSpy, client };
}

describe("QuotaIndicator", () => {
  afterEach(cleanup);

  it("renders a compact pill without per-dimension details", async () => {
    renderIndicator([videoItem()]);
    expect(await screen.findByLabelText("我的额度")).toBeTruthy();
    expect(screen.getByText("额度")).toBeTruthy();
    // 胶囊只放图标 + 额度 + 下拉箭头：维度名与百分比收进下拉
    expect(screen.queryByText("视频资源")).toBeNull();
    expect(screen.queryByText("12%")).toBeNull();
  });

  it("renders nothing when no dimensions are configured", async () => {
    const { container } = renderIndicator([]);
    await waitFor(() => expect(fetchQuotaMe).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("shows a warning tone for exceeded status", async () => {
    const { container } = renderIndicator(
      [
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
      ],
      false,
      "exceeded",
    );
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
    expect(container.querySelector(".text-red-600")).toBeNull();
    // 不再有进度条
    expect(container.querySelector("[role='progressbar']")).toBeNull();

    const user = userEvent.setup();
    await user.click(screen.getByLabelText("我的额度"));
    expect(await screen.findByText("5")).toBeTruthy();
  });

  it("hides limit display for record-only dimensions with a configured limit", async () => {
    const { container } = renderIndicator([
      videoItem({
        status: "unlimited",
        videos: {
          enforced: false,
          limit: 50,
          used: 5,
          reserved: null,
          remaining: 45,
          ratio: null,
          status: "unlimited",
          unit: "points",
        },
      }),
    ]);
    await screen.findByLabelText("我的额度");

    const user = userEvent.setup();
    await user.click(screen.getByLabelText("我的额度"));
    expect(await screen.findByText("5")).toBeTruthy();
    // 不限额只显示实际用量，不显示 / limit
    expect(screen.queryByText("5/50")).toBeNull();
    expect(container.querySelector("[role='progressbar']")).toBeNull();
  });

  it("renders dimensions in backend order with per-row period badges", async () => {
    // 后端已按 模型组 → 生图 → 视频 排序，前端按返回顺序渲染
    renderIndicator([modelItem(), imageItem(), videoItem()]);
    await screen.findByLabelText("我的额度");
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("我的额度"));

    const model = screen.getByText("高级模型");
    const image = screen.getByText("生图资源");
    const video = screen.getByText("视频资源");
    expect(
      model.compareDocumentPosition(image) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      image.compareDocumentPosition(video) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // 限额维度展示 used/limit
    expect(screen.getByText("12/200")).toBeTruthy();

    // 周期徽标在行尾，完整区间在 title 提示里
    expect(screen.getAllByText("每周")).toHaveLength(2);
    expect(screen.getAllByText("每月")).toHaveLength(1);
    const [firstWeekly] = screen.getAllByText("每周");
    expect(firstWeekly?.getAttribute("title")).toBe(
      "每周 2026-09-01~2026-09-07",
    );
  });

  it("lists model dimensions in the dropdown with matching icons", async () => {
    renderIndicator([videoItem(), modelItem()]);
    await screen.findByLabelText("我的额度");

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
