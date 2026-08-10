/**
 * E2E-011: Smoke Tests
 *
 * Verifies the application is up and the core user flow works:
 * 1. App loads and shows login page
 * 2. Health endpoint is reachable
 * 3. Login with valid credentials succeeds
 * 4. Dashboard renders after login
 * 5. Sidebar navigation links are present
 * 6. Logout redirects to login page
 */

import { test, expect } from '@playwright/test';
import { TEST_CONFIG } from '../config/test-config';
import { LoginPage } from '../pages/login.page';
import { DashboardPage } from '../pages/dashboard.page';
import { CommonPage, SIDEBAR_NAV } from '../pages/common.page';

test.describe('Smoke Tests', () => {
  test('app loads and shows login page', async ({ page }) => {
    await page.goto('/');

    // Unauthenticated user should be redirected to /login
    await page.waitForURL('/login', { timeout: 15_000 });

    // Login form should be visible
    const usernameInput = page.locator('input[name="username"], input[id="username"]');
    const passwordInput = page.locator('input[name="password"], input[id="password"]');
    const submitBtn = page.locator('button[type="submit"]');

    await expect(usernameInput).toBeVisible();
    await expect(passwordInput).toBeVisible();
    await expect(submitBtn).toBeVisible();
  });

  test('health endpoint is reachable', async ({ request }) => {
    // Check the backend health endpoint directly
    const response = await request.get(`${TEST_CONFIG.apiUrl}/api/health`);
    expect(response.ok()).toBeTruthy();

    const body = await response.json();
    expect(body).toHaveProperty('status');
  });

  test('login with valid credentials succeeds', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;

    await loginPage.login(username, password);

    // Should redirect away from login
    await page.waitForURL((url) => !url.pathname.includes('/login'), {
      timeout: 15_000,
    });

    // Should be on dashboard (root)
    expect(page.url()).toMatch(/\/$/);
  });

  test('dashboard renders after login', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const dashboardPage = new DashboardPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;

    await loginPage.loginViaApi(username, password);

    // Dashboard should load
    const isLoaded = await dashboardPage.isLoaded();
    expect(isLoaded).toBeTruthy();

    // Should have at least one widget/card
    const widgetCount = await dashboardPage.getWidgetCount();
    expect(widgetCount).toBeGreaterThan(0);
  });

  test('sidebar navigation links are present', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const commonPage = new CommonPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;

    await loginPage.loginViaApi(username, password);

    // Core nav links should be visible (admin sees everything)
    const allLinks = [
      ...SIDEBAR_NAV.observe,
      ...SIDEBAR_NAV.build,
      ...SIDEBAR_NAV.operate,
      ...SIDEBAR_NAV.settings,
    ];

    for (const item of allLinks) {
      const isVisible = await commonPage.isSidebarLinkVisible(item.label);
      expect(isVisible, `Sidebar link "${item.label}" should be visible for admin`).toBeTruthy();
    }
  });

  test('logout redirects to login page', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const commonPage = new CommonPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;

    await loginPage.loginViaApi(username, password);

    // Logout
    await commonPage.logout();

    // Should be on login page
    await page.waitForURL('/login', { timeout: 15_000 });
    const isLogin = await loginPage.isLoginPage();
    expect(isLogin).toBeTruthy();

    // Visiting a protected page should redirect back to login
    await page.goto('/projects');
    await page.waitForURL('/login', { timeout: 15_000 });
  });
});
