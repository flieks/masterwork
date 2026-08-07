import { defineConfig, devices } from '@playwright/test';

// End-to-end tests. These run against a REAL backend + dev server.
// They are skipped unless BACKEND_URL is set (see tests/e2e/*.spec.ts), so the
// default `npm run test:e2e` is a no-op in CI / offline dev.
const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8008';
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:5192';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.BACKEND_URL
    ? {
        command: 'npm run dev',
        url: baseURL,
        reuseExistingServer: true,
        timeout: 60_000,
        env: { VITE_API_URL: backendUrl },
      }
    : undefined,
});
