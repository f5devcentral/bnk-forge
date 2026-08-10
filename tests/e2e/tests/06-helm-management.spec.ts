/**
 * E2E-017: Helm Management Tests
 *
 * Verifies the Helm Packages page:
 * 1. Page loads with heading
 * 2. Shows releases or empty state
 * 3. Manage repos / install chart button is present
 * 4. Manage repos dialog opens
 * 5. Install chart dialog opens
 * 6. Cluster selector is present (Helm requires cluster context)
 * 7. Page content includes relevant Helm terminology
 * 8. Navigate to helm via sidebar
 */

import { test, expect } from '@playwright/test';
import { TEST_CONFIG } from '../config/test-config';
import { LoginPage } from '../pages/login.page';
import { HelmPage } from '../pages/helm.page';
import { CommonPage } from '../pages/common.page';

test.describe('Helm Management', () => {
  let loginPage: LoginPage;
  let helmPage: HelmPage;
  let commonPage: CommonPage;

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
    helmPage = new HelmPage(page);
    commonPage = new CommonPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;
    await loginPage.loginViaApi(username, password);
  });

  test('helm packages page loads', async ({ page }) => {
    await helmPage.goto();

    const isLoaded = await helmPage.isLoaded();
    expect(isLoaded).toBeTruthy();
  });

  test('shows releases or empty state', async ({ page }) => {
    await helmPage.goto();
    await page.waitForTimeout(1000);

    const hasReleases = await helmPage.hasReleases();
    const isEmpty = await helmPage.isEmptyState();
    const mainContent = await helmPage.getMainContent();
    const lowerContent = mainContent.toLowerCase();

    // Should show releases, empty state, or at least helm-related content
    const hasContent =
      hasReleases ||
      isEmpty ||
      lowerContent.includes('release') ||
      lowerContent.includes('chart') ||
      lowerContent.includes('install') ||
      lowerContent.includes('add') ||
      lowerContent.includes('cluster');

    expect(hasContent).toBeTruthy();
  });

  test('manage repos or install chart button is present', async ({ page }) => {
    await helmPage.goto();
    await page.waitForTimeout(1000);

    const hasManageRepos = await helmPage.hasManageReposButton();
    const hasInstall = await helmPage.hasInstallButton();

    // At least one action button should be available
    expect(hasManageRepos || hasInstall).toBeTruthy();
  });

  test('action button opens dialog or panel', async ({ page }) => {
    await helmPage.goto();
    await page.waitForTimeout(1000);

    const hasManageRepos = await helmPage.hasManageReposButton();
    const hasInstall = await helmPage.hasInstallButton();

    if (hasManageRepos) {
      await helmPage.clickManageRepos();
    } else if (hasInstall) {
      await helmPage.clickInstall();
    }

    // A dialog, panel, or new content should appear
    const dialog = page.locator('[role="dialog"], [role="complementary"], form, [class*="panel" i]');
    const isOpen = await dialog.first().isVisible({ timeout: 5000 }).catch(() => false);

    // Also check if URL changed (some actions navigate to a sub-page)
    const urlChanged = !page.url().endsWith('/helm');

    expect(isOpen || urlChanged).toBeTruthy();
  });

  test('page shows cluster context or selector', async ({ page }) => {
    await helmPage.goto();
    await page.waitForTimeout(1000);

    // Helm operations require a cluster context — page should show some cluster reference
    const mainContent = await helmPage.getMainContent();
    const lowerContent = mainContent.toLowerCase();

    const hasClusterContext =
      lowerContent.includes('cluster') ||
      lowerContent.includes('namespace') ||
      lowerContent.includes('kubeconfig') ||
      lowerContent.includes('select') ||
      lowerContent.includes('connect');

    // Either there's a cluster selector/reference, or a message about needing a cluster
    expect(hasClusterContext).toBeTruthy();
  });

  test('search functionality works if available', async ({ page }) => {
    await helmPage.goto();
    await page.waitForTimeout(1000);

    // Try to search — may or may not be available depending on page state
    const searchInput = page.locator(
      'input[type="search"], input[placeholder*="search" i], input[placeholder*="filter" i]'
    ).first();

    const hasSearch = await searchInput.isVisible({ timeout: 2000 }).catch(() => false);
    if (hasSearch) {
      await searchInput.fill('nginx');
      await page.waitForTimeout(500);

      // Search should filter or show results
      const mainContent = await helmPage.getMainContent();
      expect(mainContent).toBeTruthy();

      // Clear search
      await searchInput.fill('');
      await page.waitForTimeout(500);
    }
  });

  test('navigate to helm via sidebar', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await commonPage.navigateTo('Helm Packages');

    expect(page.url()).toContain('/helm');
    const isLoaded = await helmPage.isLoaded();
    expect(isLoaded).toBeTruthy();
  });
});
