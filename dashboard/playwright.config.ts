import { defineConfig, devices } from "@playwright/test";

const port = process.env.E2E_PORT ?? "8011";
const baseURL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e/tests",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "e2e-report" }]],
  outputDir: "e2e-results",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  globalSetup: "./e2e/support/global-setup.ts",
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: "bash e2e/start-backend.sh",
        url: `${baseURL}/health`,
        reuseExistingServer: false,
        timeout: 60_000,
      },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
