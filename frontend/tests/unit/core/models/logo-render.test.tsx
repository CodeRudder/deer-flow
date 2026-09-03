import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";

import { ModelProviderLogo } from "@/core/models/logo";

afterEach(cleanup);

describe("ModelProviderLogo", () => {
  it("renders the brand icon for providers with a Color variant", () => {
    const { container } = render(<ModelProviderLogo name="deepseek-v3" />);
    expect(container.querySelector("[data-provider='deepseek']")).toBeTruthy();
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("falls back to the mono icon for providers without a Color variant", () => {
    const { container } = render(<ModelProviderLogo name="gpt-5" />);
    expect(container.querySelector("[data-provider='openai']")).toBeTruthy();
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("renders the generic sparkle fallback for unknown models", () => {
    const { container } = render(<ModelProviderLogo name="unknown-model" />);
    // 无匹配 provider：不输出品牌标记，仅渲染通用兜底图标
    expect(container.querySelector("[data-provider]")).toBeNull();
    expect(container.querySelector("svg")).toBeTruthy();
  });
});
