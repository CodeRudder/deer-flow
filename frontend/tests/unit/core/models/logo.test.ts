import { expect, test } from "@rstest/core";

import { resolveProviderFromModelName } from "@/core/models/logo";

test("resolves providers from model names and display names", () => {
  expect(resolveProviderFromModelName("claude-sonnet-4")).toBe("anthropic");
  expect(resolveProviderFromModelName("wan2.7-image-pro")).toBe("alibaba-cn");
  expect(resolveProviderFromModelName("minimax-h3")).toBe("minimax");
  expect(resolveProviderFromModelName("custom-model", "GLM-5.3")).toBe(
    "zhipuai",
  );
  expect(resolveProviderFromModelName("kimi-k2")).toBe("moonshotai-cn");
  expect(resolveProviderFromModelName("doubao-seed-1.6")).toBe("doubao");
  expect(resolveProviderFromModelName("deepseek-v3")).toBe("deepseek");
});

test("returns null for unknown providers", () => {
  expect(resolveProviderFromModelName("unconfigured-provider")).toBeNull();
});
