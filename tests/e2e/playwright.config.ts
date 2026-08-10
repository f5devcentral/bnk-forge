import { defineConfig, devices } from '@playwright/test';
import { TEST_CONFIG } from './config/test-config';

/**
 * Playwright E2E Test Configuration
 *
 * Two tiers:
 *   - Tier 1 (default): UI workflow tests, ~5 min, CI-safe
 *   - Tier 2: Full infrastructure deployment tests, ~30-60 min, manual/nightly
 *
 * See https://playwright.dev/docs/test-configuration
 */
export default defineConfig({
  testDir: './tests',

  // Ignore tier2 directory by default — run with: npx playwright test tests/tier2/
  testIgnore: process.env.E2E_TIER === '2' ? undefined : '**/tier2/**',

  // Global setup to disable onboarding
  globalSetup: require.resolve('./global-setup'),

  // Tier 1 timeout: 60s per test (UI workflows are fast)
  // Tier 2 timeout: 10 min (terraform apply)
  timeout: process.env.E2E_TIER === '2'
    ? TEST_CONFIG.timeouts.terraformApply
    : 60_000,

  // Expect timeout for assertions
  expect: {
    timeout: process.env.E2E_TIER === '2'
      ? TEST_CONFIG.timeouts.mediumWait
      : 10_000,
  },

  // Run tests in serial (one at a time) to avoid resource conflicts
  fullyParallel: false,
  workers: 1,

  // Fail the build on CI if you accidentally left test.only in the source code
  forbidOnly: !!process.env.CI,

  // Retry failed tests
  retries: process.env.CI ? 2 : 0,

  // Reporter configuration
  reporter: [
    ['html', { outputFolder: 'playwright-report' }],
    ['json', { outputFile: 'test-results/results.json' }],
    ['list'],
  ],

  // Shared settings for all projects
  use: {
    // Base URL for navigation
    baseURL: TEST_CONFIG.baseUrl,

    // Collect trace on failure
    trace: 'retain-on-failure',

    // Screenshot on failure
    screenshot: 'only-on-failure',

    // Video on failure
    video: 'retain-on-failure',

    // Navigation timeout
    navigationTimeout: TEST_CONFIG.timeouts.navigation,

    // Action timeout
    actionTimeout: 15_000,
  },

  // Configure projects for different browsers
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },

    // Uncomment to test on other browsers
    // {
    //   name: 'firefox',
    //   use: { ...devices['Desktop Firefox'] },
    // },
    // {
    //   name: 'webkit',
    //   use: { ...devices['Desktop Safari'] },
    // },
  ],

  // Run local dev server before starting tests (optional)
  // Uncomment if you want tests to start the application automatically
  // webServer: {
  //   command: 'docker-compose up',
  //   url: TEST_CONFIG.baseUrl,
  //   reuseExistingServer: !process.env.CI,
  //   timeout: 120000,
  // },
});
