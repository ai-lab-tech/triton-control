import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  fullyParallel: false,
  retries: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.SMOKE_BACKEND_URL ?? "http://127.0.0.1:8000",
    extraHTTPHeaders: {
      "content-type": "application/json",
    },
  },
});
