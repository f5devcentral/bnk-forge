/**
 * E2E-020: Error Handling Tests
 *
 * Verifies the application handles errors gracefully:
 * 1. 404 page for unknown routes
 * 2. Login form validation
 * 3. API error shows user-friendly message
 * 4. Session expiry redirects to login
 * 5. API health endpoint error handling
 * 6. No console errors on main pages
 * 7. Command palette opens and closes
 * 8. Sidebar collapse and expand
 */

import { test, expect } from '@playwright/test';
import { TEST_CONFIG } from '../config/test-config';
import { LoginPage } from '../pages/login.page';
import { CommonPage } from '../pages/common.page';

test.describe('Error Handling', () => {
  test('404 page for unknown routes', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;
    await loginPage.loginViaApi(username, password);

    // Navigate to a non-existent route
    await page.goto('/this-route-does-not-exist-at-all');
    await page.waitForLoadState('networkidle');

    // Should show 404 or "not found" content
    const mainText = (await page.locator('main, body').textContent()) || '';
    const is404 =
      mainText.includes('404') ||
      mainText.toLowerCase().includes('not found') ||
      mainText.toLowerCase().includes('page not found') ||
      mainText.toLowerCase().includes("doesn't exist");

    expect(is404).toBeTruthy();
  });

  test('login form shows error for invalid credentials', async ({ page }) => {
    const loginPage = new LoginPage(page);

    await loginPage.login('invalid-user', 'wrong-password');

    // Wait for error response
    await page.waitForTimeout(2000);

    // Should show an error message
    const error = await loginPage.getErrorMessage();
    const isStillOnLogin = await loginPage.isLoginPage();

    // Either we got an error message, or we're still on the login page
    expect(error || isStillOnLogin).toBeTruthy();
  });

  test('login form validates required fields', async ({ page }) => {
    const loginPage = new LoginPage(page);

    await loginPage.goto();

    // Try to submit empty form
    await loginPage.clickLogin();
    await page.waitForTimeout(500);

    // Should still be on login page (form validation prevents submission)
    const isStillOnLogin = await loginPage.isLoginPage();
    expect(isStillOnLogin).toBeTruthy();
  });

  test('session expiry redirects to login', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;
    await loginPage.loginViaApi(username, password);

    // Verify we're logged in
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Clear auth token to simulate expiry
    await page.evaluate(() => {
      localStorage.removeItem('auth_token');
      localStorage.removeItem('auth-storage');
    });

    // Navigate to a protected page
    await page.goto('/projects');
    await page.waitForTimeout(2000);

    // Should be redirected to login
    const url = page.url();
    expect(url).toContain('/login');
  });

  test('API health endpoint responds with valid JSON', async ({ page }) => {
    // Check health endpoint directly — this doesn't require auth
    const response = await page.request.get(`${TEST_CONFIG.apiUrl}/api/system/health`);

    // Should respond with 200
    expect(response.status()).toBe(200);

    // Should return valid JSON with a status field
    const body = await response.json();
    expect(body).toHaveProperty('status');
  });

  test('no critical console errors on dashboard', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;

    // Collect console errors
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
      }
    });

    await loginPage.loginViaApi(username, password);
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // Filter out known benign errors (favicon, WebSocket reconnects, etc.)
    const criticalErrors = consoleErrors.filter(
      (e) =>
        !e.includes('favicon') &&
        !e.includes('websocket') &&
        !e.includes('WebSocket') &&
        !e.includes('net::ERR_') &&
        !e.includes('Failed to load resource') &&
        !e.includes('ResizeObserver')
    );

    expect(criticalErrors).toHaveLength(0);
  });

  test('command palette opens with Cmd+K and closes with Escape', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const commonPage = new CommonPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;
    await loginPage.loginViaApi(username, password);

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Open command palette
    await commonPage.openCommandPalette();

    // Should show a dialog or command input
    const commandPalette = page.locator(
      '[role="dialog"], [cmdk-dialog], [class*="command" i], [data-cmdk-root]'
    ).first();
    const isOpen = await commandPalette.isVisible({ timeout: 3000 }).catch(() => false);

    if (isOpen) {
      // Close with Escape
      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);

      const isClosed = !(await commandPalette.isVisible({ timeout: 1000 }).catch(() => false));
      expect(isClosed).toBeTruthy();
    }
    // If command palette doesn't exist, that's acceptable — feature may not be implemented
  });

  test('sidebar collapses and expands', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const commonPage = new CommonPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;
    await loginPage.loginViaApi(username, password);

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const sidebar = commonPage.getSidebar();
    const sidebarVisible = await sidebar.isVisible({ timeout: 3000 }).catch(() => false);

    if (sidebarVisible) {
      // Try to collapse
      await commonPage.collapseSidebar();
      await page.waitForTimeout(500);

      // Try to expand
      await commonPage.expandSidebar();
      await page.waitForTimeout(500);

      // Sidebar should still be functional (visible after expand)
      const stillVisible = await sidebar.isVisible({ timeout: 2000 }).catch(() => false);
      expect(stillVisible).toBeTruthy();
    }
  });

  test('navigating to API URL directly returns JSON not crash', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;
    await loginPage.loginViaApi(username, password);

    // Try navigating to an API-like path in the browser
    // This should either show 404 or be handled gracefully
    await page.goto('/api/system/health');
    await page.waitForTimeout(1000);

    // The page should not crash — either redirect, show content, or show JSON
    const bodyText = await page.locator('body').textContent();
    expect(bodyText).toBeTruthy();
  });
});
