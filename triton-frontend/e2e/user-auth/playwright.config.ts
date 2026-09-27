import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const mode = process.env.AUTH_SMOKE_MODE;
if (!mode || !["smtp", "manual-link", "disabled", "oidc"].includes(mode)) {
  throw new Error("Use e2e/user-auth/run.sh to prepare an authentication smoke environment.");
}

export default defineConfig({
  testDir: ".",
  testMatch: "user-auth.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  outputDir: resolve(__dirname, `../../test-results/user-auth/${mode}`),
  reporter: [
    ["list"],
    [
      "html",
      {
        outputFolder: resolve(__dirname, `../../playwright-report/user-auth/${mode}`),
        open: "never",
      },
    ],
  ],
  use: {
    baseURL: process.env.AUTH_SMOKE_URL,
    viewport: { width: 1600, height: 1100 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
});
