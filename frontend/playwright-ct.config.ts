import { fileURLToPath, URL } from 'node:url';
import { defineConfig, devices } from '@playwright/experimental-ct-react';
import react from '@vitejs/plugin-react';

// Component tests. The Vite config is inlined here (ctViteConfig) — CT ships its
// own bundler. Tailwind is intentionally omitted: CT verifies behaviour, not
// styling, and @tailwindcss/vite clashes with the CT-bundled Vite types.
export default defineConfig({
  testDir: './tests/components',
  testMatch: /.*\.ct\.tsx$/,
  snapshotDir: './tests/components/__snapshots__',
  timeout: 20_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  use: {
    trace: 'on-first-retry',
    ctPort: 3110,
    ctViteConfig: {
      // reason: CT bundles an older Vite type than the app's Vite 6; the plugin
      // array is structurally compatible at runtime but not at the type level.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      plugins: [react()] as any,
      resolve: {
        alias: {
          '~': fileURLToPath(new URL('./src', import.meta.url)),
        },
      },
    },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
