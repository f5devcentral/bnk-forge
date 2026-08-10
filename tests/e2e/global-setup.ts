/**
 * Global Setup - Runs before all tests
 *
 * Sets up localStorage to skip onboarding dialog
 */

import { chromium, FullConfig } from '@playwright/test';
import { TEST_CONFIG } from './config/test-config';

async function globalSetup(config: FullConfig) {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  // Navigate to app to establish context
  await page.goto(TEST_CONFIG.baseUrl);

  // Set localStorage to mark onboarding as complete
  await page.evaluate(() => {
    localStorage.setItem('bnk-forge-onboarding-complete', 'true');
  });

  console.log('✓ Global setup complete - onboarding dialog disabled');

  await browser.close();
}

export default globalSetup;
