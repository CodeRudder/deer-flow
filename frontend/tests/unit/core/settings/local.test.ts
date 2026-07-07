import { expect, test } from "@rstest/core";

import {
  DEFAULT_LOCAL_SETTINGS,
  applyThreadContextOverrides,
} from "@/core/settings/local";

test("defaults token usage to header total plus per-turn breakdown", () => {
  expect(DEFAULT_LOCAL_SETTINGS.tokenUsage).toEqual({
    headerTotal: true,
    inlineMode: "per_turn",
  });
});

test("defaults vision model context to undefined", () => {
  expect(DEFAULT_LOCAL_SETTINGS.context.vision_model_name).toBeUndefined();
});

test("applies thread chat and vision model overrides", () => {
  const settings = applyThreadContextOverrides(
    DEFAULT_LOCAL_SETTINGS,
    "chat-model",
    "vision-model",
  );

  expect(settings.context.model_name).toBe("chat-model");
  expect(settings.context.vision_model_name).toBe("vision-model");
});
