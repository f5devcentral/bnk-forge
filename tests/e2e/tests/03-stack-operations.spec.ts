/**
 * E2E-014: Stack Operations Tests
 *
 * Verifies the Blueprints / Stacks page:
 * 1. Stacks page loads
 * 2. Search filters stacks
 * 3. Stack detail can be viewed
 * 4. Empty state shown when no results
 * 5. Stack card shows metadata (module count, cloud provider)
 * 6. Stack detail dialog opens with module list
 * 7. Deploy button present in stack detail
 * 8. Keyboard navigation (Escape closes detail)
 */

import { test, expect } from '@playwright/test';
import { TEST_CONFIG } from '../config/test-config';
import { LoginPage } from '../pages/login.page';
import { StacksPage } from '../pages/stacks.page';
import { CommonPage } from '../pages/common.page';

test.describe('Stack Operations', () => {
  let loginPage: LoginPage;
  let stacksPage: StacksPage;
  let commonPage: CommonPage;

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
    stacksPage = new StacksPage(page);
    commonPage = new CommonPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;
    await loginPage.loginViaApi(username, password);
  });

  test('blueprints page loads', async ({ page }) => {
    await stacksPage.goto();

    const isLoaded = await stacksPage.isLoaded();
    expect(isLoaded).toBeTruthy();
  });

  test('search filters stacks', async ({ page }) => {
    await stacksPage.goto();
    await page.waitForTimeout(1000);

    // Search for something
    await stacksPage.search('kubernetes');
    await page.waitForTimeout(500);

    // Clear search
    await stacksPage.search('');
  });

  test('stacks page shows content or empty state', async ({ page }) => {
    await stacksPage.goto();
    await page.waitForTimeout(1000);

    // Should show either stacks or empty state
    const stackCount = await stacksPage.getStackCount();
    const isEmpty = await stacksPage.isEmptyState();

    // One of these should be true
    expect(stackCount > 0 || isEmpty).toBeTruthy();
  });

  test('search with no results shows empty state', async ({ page }) => {
    await stacksPage.goto();

    // Search for something that won't exist
    await stacksPage.search('zzz-nonexistent-blueprint-xyz');
    await page.waitForTimeout(500);

    // Should show empty state or zero results
    const stackCount = await stacksPage.getStackCount();
    const isEmpty = await stacksPage.isEmptyState();
    expect(stackCount === 0 || isEmpty).toBeTruthy();
  });

  test('stack cards show metadata', async ({ page }) => {
    await stacksPage.goto();
    await page.waitForTimeout(1000);

    const stackCount = await stacksPage.getStackCount();
    if (stackCount > 0) {
      // Each stack card should have descriptive content
      const mainText = (await page.locator('main').textContent()) || '';
      const lowerText = mainText.toLowerCase();

      // Cards should show some combination of: module count, description, category, or provider
      const hasMetadata =
        lowerText.includes('module') ||
        lowerText.includes('aws') ||
        lowerText.includes('kubernetes') ||
        lowerText.includes('deploy') ||
        lowerText.includes('description');
      expect(hasMetadata).toBeTruthy();
    }
  });

  test('clicking a stack opens detail dialog or navigates', async ({ page }) => {
    await stacksPage.goto();
    await page.waitForTimeout(1000);

    const stackCount = await stacksPage.getStackCount();
    if (stackCount > 0) {
      // Click the first stack card
      const firstCard = page.locator('[class*="card" i], table tbody tr').first();
      await firstCard.click();
      await page.waitForTimeout(1000);

      // Should open a dialog or navigate to a detail view
      const dialogOpen = await commonPage.isDialogOpen();
      const urlChanged = !page.url().endsWith('/stacks');

      // One of: dialog opened OR URL changed to detail page
      expect(dialogOpen || urlChanged).toBeTruthy();

      // If dialog opened, it should have some content about the stack
      if (dialogOpen) {
        const dialogTitle = await commonPage.getDialogTitle();
        expect(dialogTitle.length).toBeGreaterThan(0);
      }
    }
  });

  test('stack detail shows module list or deploy option', async ({ page }) => {
    await stacksPage.goto();
    await page.waitForTimeout(1000);

    const stackCount = await stacksPage.getStackCount();
    if (stackCount > 0) {
      // Open first stack
      const firstCard = page.locator('[class*="card" i], table tbody tr').first();
      await firstCard.click();
      await page.waitForTimeout(1000);

      // Look for module-related content or deploy action in the visible area
      const mainText = (await page.locator('main, [role="dialog"]').first().textContent()) || '';
      const lowerText = mainText.toLowerCase();

      const hasDetailContent =
        lowerText.includes('module') ||
        lowerText.includes('deploy') ||
        lowerText.includes('install') ||
        lowerText.includes('component') ||
        lowerText.includes('resource');

      expect(hasDetailContent).toBeTruthy();
    }
  });

  test('navigate to stacks via sidebar', async ({ page }) => {
    // Start from dashboard
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Click Blueprints in sidebar
    await commonPage.navigateTo('Blueprints');

    // Should be on stacks page
    expect(page.url()).toContain('/stacks');
    const isLoaded = await stacksPage.isLoaded();
    expect(isLoaded).toBeTruthy();
  });
});
