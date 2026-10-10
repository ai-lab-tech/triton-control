import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "plugins.spec.ts",
  timeout: 180_000,
  expect: { timeout: 30_000 },
  workers: 1,
  retries: 0,
  outputDir: `../../test-results/code-server/${process.env.PLUGIN_SMOKE_PHASE || "baseline"}`,
  reporter: [
    ["list"],
    [
      "html",
      {
        outputFolder: `../../playwright-report/code-server/${process.env.PLUGIN_SMOKE_PHASE || "baseline"}`,
        open: "never",
      },
    ],
  ],
  use: {
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
    baseURL: process.env.PLUGIN_SMOKE_URL || "http://localhost:18080",
    viewport: { width: 1600, height: 1100 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
});
