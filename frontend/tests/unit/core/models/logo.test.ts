import { expect, test } from "@rstest/core";

import { getModelLogoPath } from "@/core/models/logo";

test("resolves bundled provider logos", () => {
  expect(getModelLogoPath("zhipuai")).toBe("/logos/zhipuai.svg");
  expect(getModelLogoPath("anthropic")).toBe("/logos/anthropic.svg");
  expect(getModelLogoPath("deepseek")).toBe("/logos/deepseek.svg");
});

test("normalizes supported provider aliases", () => {
  expect(getModelLogoPath(" Google-Vertex ")).toBe("/logos/google.svg");
  expect(getModelLogoPath("zai-coding-plan")).toBe("/logos/zhipuai.svg");
});

test("does not construct paths for unknown providers", () => {
  expect(getModelLogoPath("unconfigured-provider")).toBeUndefined();
  expect(getModelLogoPath("../../external-logo")).toBeUndefined();
});
