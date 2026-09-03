import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ImageGenerationSelector } from "@/components/workspace/image-generation-selector";

const useImageGenerationProviders = rs.hoisted(() => rs.fn());

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { loading: "加载中" },
      inputBox: {
        imageGeneration: "图像生成",
        imageGenerationNotConfigured: "未配置",
        imageGenerationSkillDisabled: "图像生成技能未启用",
        imageGenerationLoadFailed: "加载失败",
      },
    },
  }),
}));

rs.mock("@/core/image-generation", () => ({ useImageGenerationProviders }));

afterEach(cleanup);

const configuredProvider = {
  name: "minimax",
  display_name: "MiniMax",
  configured: true,
  models: [
    {
      name: "minimax-h3",
      display_name: "MiniMax H3",
      description: "文生图模型",
    },
  ],
};

function renderSelector(
  selection: { image_generation_model?: string } = {},
  providers = [configuredProvider],
) {
  useImageGenerationProviders.mockReturnValue({
    data: { skill_enabled: true, providers },
    providers,
    isLoading: false,
    error: null,
  });
  const onSelectionChange = rs.fn();
  const view = render(
    <ImageGenerationSelector
      selection={selection}
      onSelectionChange={onSelectionChange}
    />,
  );
  return { ...view, onSelectionChange };
}

async function openMenu() {
  const user = userEvent.setup();
  await user.click(screen.getByLabelText("图像生成"));
  await screen.findByRole("menuitem", { name: /MiniMax H3/ });
  return user;
}

describe("ImageGenerationSelector", () => {
  it("treats the first configured model as default and shows it on the trigger", async () => {
    renderSelector();
    await openMenu();

    // 无显式选择时，默认真实模型行打勾，触发按钮显示其名称
    expect(document.querySelectorAll(".lucide-check").length).toBe(1);
    expect(document.querySelector("[data-provider='minimax']")).toBeTruthy();
    expect(screen.getByLabelText("图像生成").textContent).toContain(
      "MiniMax H3",
    );
  });

  it("marks the selected model row and emits its name on click", async () => {
    const { onSelectionChange } = renderSelector({
      image_generation_model: "minimax-h3",
    });
    const user = await openMenu();

    // 有选择时改为模型行打勾
    expect(document.querySelectorAll(".lucide-check").length).toBe(1);
    await user.click(screen.getByRole("menuitem", { name: /MiniMax H3/ }));
    expect(onSelectionChange).toHaveBeenCalledWith({
      image_generation_model: "minimax-h3",
    });
  });

  it("keeps the trigger unfilled while a model is selected", () => {
    renderSelector({ image_generation_model: "minimax-h3" });
    expect(
      screen.getByLabelText("图像生成").classList.contains("bg-accent"),
    ).toBe(false);
  });

  it("keeps the generic icon on the trigger when a model is selected", () => {
    renderSelector({ image_generation_model: "minimax-h3" });
    // 触发按钮保持通用图像图标，品牌 logo 只出现在菜单行里
    expect(document.querySelector("[data-provider='minimax']")).toBeNull();
    expect(
      screen.getByLabelText("图像生成").querySelector(".lucide-image"),
    ).toBeTruthy();
  });

  it("keeps unconfigured providers visible but unselectable", async () => {
    const { onSelectionChange } = renderSelector({}, [
      { ...configuredProvider, configured: false },
    ]);
    const user = await openMenu();

    expect(screen.getByText("未配置")).toBeTruthy();
    await user.click(screen.getByRole("menuitem", { name: /MiniMax H3/ }));
    expect(onSelectionChange).not.toHaveBeenCalled();
  });
});
