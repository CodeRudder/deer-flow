import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { ImageIcon } from "lucide-react";

import { PromptInputActionMenu } from "@/components/ai-elements/prompt-input";
import {
  ModelMenuTrigger,
  ModelOptionContent,
  modelOptionRowClassName,
} from "@/components/workspace/model-menu";

afterEach(cleanup);

describe("ModelOptionContent", () => {
  it("shows a check only for the selected row", () => {
    const { rerender } = render(
      <ModelOptionContent label="MiniMax H3" selected />,
    );
    expect(document.querySelector(".lucide-check")).toBeTruthy();

    rerender(<ModelOptionContent label="MiniMax H3" selected={false} />);
    expect(document.querySelector(".lucide-check")).toBeNull();
  });

  it("renders the logo slot only when provided", () => {
    const { rerender, container } = render(
      <ModelOptionContent
        label="MiniMax H3"
        logo={<span data-testid="logo" />}
        selected={false}
      />,
    );
    expect(container.querySelector("[data-testid='logo']")).toBeTruthy();

    rerender(<ModelOptionContent label="MiniMax H3" selected={false} />);
    expect(container.querySelector("[data-testid='logo']")).toBeNull();
  });

  it("swaps the check slot for the unconfigured label", () => {
    render(
      <ModelOptionContent
        configured={false}
        label="MiniMax H3"
        notConfiguredLabel="未配置"
        selected
      />,
    );
    expect(screen.getByText("未配置")).toBeTruthy();
    expect(document.querySelector(".lucide-check")).toBeNull();
  });

  it("renders meta alongside the check", () => {
    render(
      <ModelOptionContent
        label="MiniMax H3"
        meta={<span>1 积分/秒起</span>}
        selected
      />,
    );
    expect(screen.getByText("1 积分/秒起")).toBeTruthy();
    expect(document.querySelector(".lucide-check")).toBeTruthy();
  });
});

describe("modelOptionRowClassName", () => {
  it("applies the shared row height and selection tone", () => {
    expect(modelOptionRowClassName(true)).toContain("h-9");
    expect(modelOptionRowClassName(true)).toContain("text-accent-foreground");
    expect(modelOptionRowClassName(false)).toContain(
      "text-muted-foreground/75",
    );
    expect(modelOptionRowClassName(false)).not.toContain(
      "text-accent-foreground",
    );
  });

  it("dims unconfigured rows", () => {
    expect(modelOptionRowClassName(false, false)).toContain("opacity-50");
    expect(modelOptionRowClassName(true, true)).not.toContain("opacity-50");
  });
});

describe("ModelMenuTrigger", () => {
  it("shows the selected model name only after a selection", () => {
    // 真实用法位于 PromptInputActionMenu 内，测试保持同一结构
    const { rerender, container } = render(
      <PromptInputActionMenu>
        <ModelMenuTrigger icon={ImageIcon} label="图像生成" />
      </PromptInputActionMenu>,
    );
    expect(screen.getByLabelText("图像生成")).toBeTruthy();
    expect(container.textContent).not.toContain("MiniMax");

    rerender(
      <PromptInputActionMenu>
        <ModelMenuTrigger
          icon={ImageIcon}
          label="图像生成"
          selectedLabel="MiniMax H3"
        />
      </PromptInputActionMenu>,
    );
    expect(screen.getByText("MiniMax H3")).toBeTruthy();
  });
});
