/**
 * Auth Fixture
 *
 * Provides a reusable authenticated page for Tier 1 E2E tests.
 * Logs in via the API and injects token into localStorage.
 */

import { test as base, Page } from '@playwright/test';
import { TEST_CONFIG } from '../config/test-config';
import { LoginPage } from '../pages/login.page';
import { CommonPage } from '../pages/common.page';

/**
 * Extended test fixture that provides an authenticated page context.
 */
export const test = base.extend<{
  /** An authenticated page (logged in as admin) */
  authedPage: Page;
  /** Login page object */
  loginPage: LoginPage;
  /** Common page utilities */
  commonPage: CommonPage;
}>({
  authedPage: async ({ page }, use) => {
    const loginPage = new LoginPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;
    await loginPage.loginViaApi(username, password);
    await use(page);
  },

  loginPage: async ({ page }, use) => {
    await use(new LoginPage(page));
  },

  commonPage: async ({ page }, use) => {
    await use(new CommonPage(page));
  },
});

export { expect } from '@playwright/test';
