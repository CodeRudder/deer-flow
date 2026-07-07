import { afterEach, expect, rs, test } from "@rstest/core";

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "/backend",
}));

rs.mock("@/core/static-mode", () => ({
  isStaticWebsiteOnly: () => false,
}));

afterEach(() => {
  rs.unstubAllGlobals();
});

test("loadModels parses vision model list", async () => {
  const fetchMock = rs.fn(
    async () =>
      new Response(
        JSON.stringify({
          models: [
            {
              name: "chat",
              model: "gpt-test",
              display_name: "Chat",
            },
          ],
          vision_models: [
            {
              name: "doubao-vision",
              model: "doubao-seed-2.0-pro",
              display_name: "Doubao Vision",
            },
          ],
          token_usage: { enabled: true },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
  );
  rs.stubGlobal("fetch", fetchMock);

  const { loadModels } = await import("@/core/models/api");
  const result = await loadModels();

  expect(fetchMock).toHaveBeenCalledWith("/backend/api/models");
  expect(result.vision_models).toEqual([
    {
      name: "doubao-vision",
      model: "doubao-seed-2.0-pro",
      display_name: "Doubao Vision",
    },
  ]);
});
