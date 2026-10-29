import { defineConfig, devices } from "@playwright/test";

/**
 * E2E-конфиг ChessAnnotator (T-036b).
 *
 * `E2E_REAL_BACKEND=1` отключает page.route-моки (см. e2e/fixtures.ts) и
 * направляет Vite dev server на реальный backend (VITE_API_PROXY).
 * Подробности — web/e2e/README.md.
 */
const REAL_BACKEND = process.env.E2E_REAL_BACKEND === "1";
const CI = Boolean(process.env.CI);
const PORT = 5173;
const BASE_URL = `http://127.0.0.1:${PORT}`;
const API_PROXY = process.env.VITE_API_PROXY || "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "test-results",
  fullyParallel: true,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: BASE_URL,
    locale: "ru-RU",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 7"] },
    },
  ],
  webServer: {
    // Vite dev server; baseURL — строго 127.0.0.1 (RULES §11.1).
    command: `pnpm dev --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: BASE_URL,
    timeout: 180_000,
    reuseExistingServer: !CI,
    env: {
      // В mocked-режиме прокси не используется (запросы перехватываются),
      // но в real-режиме Vite должен проксировать /auth и /v1 на backend.
      VITE_API_PROXY: REAL_BACKEND ? API_PROXY : "http://127.0.0.1:8000",
      E2E_REAL_BACKEND: REAL_BACKEND ? "1" : "0",
    },
  },
});
