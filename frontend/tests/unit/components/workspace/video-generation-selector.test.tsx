import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { VideoGenerationSelector } from "@/components/workspace/video-generation-selector";

const useVideoGenerationProviders = rs.hoisted(() => rs.fn());

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { loading: "加载中" },
      inputBox: {
        videoGeneration: "视频生成",
        videoGenerationNotConfigured: "未配置",
        videoGenerationSkillDisabled: "视频生成技能未启用",
        videoGenerationLoadFailed: "加载失败",
        videoRateFromLabel: (label: string) => `低至${label}积分/秒`,
        videoPointsPerSecond: "积分/秒",
        videoRegenerationLabel: "重生成",
        videoDurationRange: "时长",
        videoDurationSeconds: "秒",
      },
    },
  }),
}));

rs.mock("@/core/video-generation", () => ({ useVideoGenerationProviders }));

afterEach(cleanup);

const billing = {
  resolutions: [
    { resolution: "1080p", yuan_per_second_min: 1, yuan_per_second_max: 2 },
    { resolution: "720p", yuan_per_second_min: 0.5, yuan_per_second_max: 1 },
  ],
  min_duration_seconds: 4,
  max_duration_seconds: 10,
};

const configuredProvider = {
  name: "minimax",
  display_name: "MiniMax",
  configured: true,
  models: [
    {
      name: "minimax-h3",
      display_name: "MiniMax H3",
      description: "视频生成模型",
      billing,
    },
  ],
};

function renderSelector(
  selection: { video_generation_model?: string } = {},
  providers = [configuredProvider],
) {
  useVideoGenerationProviders.mockReturnValue({
    data: { skill_enabled: true, providers },
    providers,
    isLoading: false,
    error: null,
  });
  const onSelectionChange = rs.fn();
  const view = render(
    <VideoGenerationSelector
      selection={selection}
      onSelectionChange={onSelectionChange}
    />,
  );
  return { ...view, onSelectionChange };
}

async function openMenu() {
  const user = userEvent.setup();
  await user.click(screen.getByLabelText("视频生成"));
  await screen.findByRole("menuitem", { name: /MiniMax H3/ });
  return user;
}

describe("VideoGenerationSelector", () => {
  it("shows the per-second rate next to the model name", async () => {
    renderSelector();
    await openMenu();

    // 跨分辨率最低价：0.5 积分/秒起；无显式选择时触发按钮显示默认真实模型
    expect(screen.getByText("低至0.5积分/秒")).toBeTruthy();
    expect(screen.getByLabelText("视频生成").textContent).toContain(
      "MiniMax H3",
    );
  });

  it("emits the model name on selection", async () => {
    const { onSelectionChange } = renderSelector();
    const user = await openMenu();

    await user.click(screen.getByRole("menuitem", { name: /MiniMax H3/ }));
    expect(onSelectionChange).toHaveBeenCalledWith({
      video_generation_model: "minimax-h3",
    });
  });

  it("hides the rate and blocks selection for unconfigured providers", async () => {
    const { onSelectionChange } = renderSelector({}, [
      { ...configuredProvider, configured: false },
    ]);
    const user = await openMenu();

    expect(screen.getByText("未配置")).toBeTruthy();
    expect(screen.queryByText("低至0.5积分/秒")).toBeNull();
    await user.click(screen.getByRole("menuitem", { name: /MiniMax H3/ }));
    expect(onSelectionChange).not.toHaveBeenCalled();
  });
});
