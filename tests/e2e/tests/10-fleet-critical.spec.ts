/**
 * E2E-CRITICAL CW-4: Fleet Health and Multi-Cluster Awareness
 *
 * Validates the Fleet page renders correctly across different states:
 * - Page loads and shows cluster overview
 * - Health cards render for connected clusters
 * - DPF/DPU tab is accessible
 * - Config promotion wizard opens
 * - Empty state renders gracefully when no clusters exist
 */

import { test, expect } from '../fixtures/auth.fixture';
import { FLEET_SCENARIOS, SCENARIO_TIMEOUTS } from '../fixtures/scenario-states';

test.describe('Fleet Critical Workflows', () => {
  test.beforeEach(async ({ authedPage }) => {
    await authedPage.goto('/fleet');
    await authedPage.waitForLoadState('networkidle');
  });

  test('fleet page loads without errors', async ({ authedPage }) => {
    // Page should render (either with clusters or empty state)
    const hasContent = await authedPage.locator('main, [role="main"], .flex-1').first().isVisible();
    expect(hasContent).toBeTruthy();

    // No unhandled error toasts
    const errorToast = authedPage.locator('[data-sonner-toast][data-type="error"]');
    await expect(errorToast).toHaveCount(0);
  });

  test('fleet overview tab is active by default', async ({ authedPage }) => {
    // The overview/clusters tab should be selected
    const activeTab = authedPage.locator('[role="tab"][aria-selected="true"], [data-state="active"]').first();
    await expect(activeTab).toBeVisible({ timeout: SCENARIO_TIMEOUTS.apiRoundtrip });
  });

  test('cluster health cards or empty state render', async ({ authedPage }) => {
    // Either cluster cards exist OR empty state is shown
    const clusterCards = authedPage.locator('[data-testid="cluster-card"], [class*="card"]').first();
    const emptyState = authedPage.locator('text=/no clusters|add.*cluster|get started|connect/i').first();

    const hasCards = await clusterCards.isVisible().catch(() => false);
    const hasEmpty = await emptyState.isVisible().catch(() => false);

    expect(hasCards || hasEmpty, 'Fleet page should show cluster cards or empty state').toBeTruthy();
  });

  test('DPF/DPU tab is accessible', async ({ authedPage }) => {
    // Navigate to DPF tab
    const dpfTab = authedPage.locator('text=/DPU Infrastructure|DPF/i').first();
    const tabExists = await dpfTab.isVisible().catch(() => false);

    if (tabExists) {
      await dpfTab.click();
      await authedPage.waitForLoadState('networkidle');

      // Should show DPF content or cluster selector
      const dpfContent = authedPage.locator('text=/DPU|infrastructure|select.*cluster/i').first();
      await expect(dpfContent).toBeVisible({ timeout: SCENARIO_TIMEOUTS.apiRoundtrip });
    } else {
      // Tab not shown — acceptable when no clusters connected
      test.skip(true, 'DPF tab not visible — likely no clusters connected');
    }
  });

  test('config promotion wizard opens from fleet', async ({ authedPage }) => {
    // Look for any "Promote" or "Config Promotion" button/link
    const promoteBtn = authedPage.locator('button:has-text("Promote"), button:has-text("Config Promotion")').first();
    const btnExists = await promoteBtn.isVisible().catch(() => false);

    if (btnExists) {
      await promoteBtn.click();
      // Wizard dialog should appear
      const dialog = authedPage.locator('[role="dialog"]');
      await expect(dialog).toBeVisible({ timeout: SCENARIO_TIMEOUTS.uiAction });
    } else {
      // No promote button — acceptable when no clusters or single cluster
      test.skip(true, 'Config promotion not available — likely insufficient clusters');
    }
  });

  test('fleet page handles navigation between tabs', async ({ authedPage }) => {
    // Find all tab triggers
    const tabs = authedPage.locator('[role="tab"]');
    const tabCount = await tabs.count();

    if (tabCount > 1) {
      // Click second tab
      await tabs.nth(1).click();
      await authedPage.waitForLoadState('networkidle');

      // Click back to first tab
      await tabs.nth(0).click();
      await authedPage.waitForLoadState('networkidle');

      // No error toasts should appear during navigation
      const errorToast = authedPage.locator('[data-sonner-toast][data-type="error"]');
      await expect(errorToast).toHaveCount(0);
    }
  });

  test('fleet page is accessible via sidebar', async ({ authedPage, commonPage }) => {
    // Navigate away first
    await authedPage.goto('/projects');
    await authedPage.waitForLoadState('networkidle');

    // Navigate back via sidebar
    const isVisible = await commonPage.isSidebarLinkVisible('Fleet');
    expect(isVisible, 'Fleet link should be in sidebar').toBeTruthy();
    await commonPage.navigateTo('Fleet');

    // Should be on fleet page
    await authedPage.waitForURL(/\/fleet/);
  });
});
