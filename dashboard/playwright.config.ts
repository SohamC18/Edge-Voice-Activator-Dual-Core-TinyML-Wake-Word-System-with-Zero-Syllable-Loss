import { defineConfig, devices } from '@playwright/test';

/**
 * Visual-validation config.
 *
 * The dashboard is judged on a projector, so the default viewport here is a
 * 1920x1080 desktop rather than a phone: if the layout only works at 1280 wide,
 * it will fail in the room. A mobile project is included because the same build
 * may be opened on a judge's phone, and the single-column fallback has to be
 * verified rather than assumed.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'projector-1080p',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } },
    },
    {
      name: 'laptop-1440',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'phone-390',
      use: { ...devices['Pixel 7'] },
    },
  ],
  webServer: {
    // Preview the production build rather than the dev server: what is validated
    // is what ships.
    command: 'npm run build && npm run preview -- --port 4173 --strictPort',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
