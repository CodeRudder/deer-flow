import { resolve } from "path";

import { pluginReact } from "@rsbuild/plugin-react";
import { defineConfig } from "@rstest/core";

export default defineConfig({
  plugins: [pluginReact()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  output: {
    // Streamdown imports KaTeX CSS as a side effect, and @lobehub/icons ships
    // an es build with extensionless relative imports that Node ESM cannot
    // load when externalized. Keep everything else externalized (a single
    // React copy from node_modules) and bundle only the problem packages.
    bundleDependencies: ["streamdown", "katex", /@lobehub[\\/]icons/],
  },
  include: ["tests/unit/**/*.test.{ts,tsx}"],
  testEnvironment: "jsdom",
});
