import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

export default defineConfig({
  testDir: ".",
  testMatch: "isolation.spec.ts",
  timeout: 1_200_000,
  expect: { timeout: 30_000 },
  workers: 1,
  retries: 0,
  outputDir: resolve(__dirname, "../../test-results/workspace-isolation"),
  reporter: [
    ["list"],
    [
      "html",
      {
        outputFolder: resolve(__dirname, "../../playwright-report/workspace-isolation"),
        open: "never",
      },
    ],
  ],
  use: {
    baseURL: process.env.ISOLATION_SMOKE_URL || "http://localhost:18080",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
