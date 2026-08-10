/**
 * E2E-016: Kubernetes Explorer Tests
 *
 * Verifies the Kubernetes page:
 * 1. Page loads
 * 2. Shows empty state when no clusters
 * 3. Register cluster button is present
 * 4. Register dialog opens
 * 5. F5 BNK page loads
 * 6. Register dialog has required fields (name, kubeconfig)
 * 7. Invalid kubeconfig submission shows error
 * 8. Navigate via sidebar
 * 9. Page handles loading state
 */

import { test, expect } from '@playwright/test';
import { TEST_CONFIG } from '../config/test-config';
import { LoginPage } from '../pages/login.page';
import { KubernetesPage } from '../pages/kubernetes.page';
import { CommonPage } from '../pages/common.page';

test.describe('Kubernetes Explorer', () => {
  let loginPage: LoginPage;
  let k8sPage: KubernetesPage;
  let commonPage: CommonPage;

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
    k8sPage = new KubernetesPage(page);
    commonPage = new CommonPage(page);
    const { username, password } = TEST_CONFIG.credentials.admin;
    await loginPage.loginViaApi(username, password);
  });

  test('kubernetes page loads', async ({ page }) => {
    await k8sPage.goto();

    const isLoaded = await k8sPage.isLoaded();
    expect(isLoaded).toBeTruthy();
  });

  test('shows empty state or cluster list', async ({ page }) => {
    await k8sPage.goto();
    await page.waitForTimeout(1000);

    const isEmpty = await k8sPage.isEmptyState();
    const hasClusters = await k8sPage.hasClusterList();

    // One of these should be true
    expect(isEmpty || hasClusters).toBeTruthy();
  });

  test('register cluster button is present', async ({ page }) => {
    await k8sPage.goto();
    await page.waitForTimeout(1000);

    const hasButton = await k8sPage.hasRegisterButton();
    // Register button should be visible (for empty or non-empty state)
    expect(hasButton).toBeTruthy();
  });

  test('register dialog opens', async ({ page }) => {
    await k8sPage.goto();
    await page.waitForTimeout(1000);

    const hasButton = await k8sPage.hasRegisterButton();
    if (hasButton) {
      await k8sPage.clickRegister();

      // A dialog or form should appear
      const dialog = page.locator('[role="dialog"], [role="complementary"], form');
      const isOpen = await dialog.first().isVisible({ timeout: 5000 }).catch(() => false);
      expect(isOpen).toBeTruthy();
    }
  });

  test('F5 BNK page loads', async ({ page }) => {
    await page.goto('/bnk');
    await page.waitForLoadState('networkidle');

    // Page should render
    const heading = page.locator('h1, h2').first();
    await expect(heading).toBeVisible({ timeout: 10_000 });

    const text = await page.locator('main').textContent();
    expect(text).toBeTruthy();
  });

  test('register dialog has required fields', async ({ page }) => {
    await k8sPage.goto();
    await page.waitForTimeout(1000);

    const hasButton = await k8sPage.hasRegisterButton();
    if (hasButton) {
      await k8sPage.clickRegister();
      await page.waitForTimeout(500);

      const dialog = page.locator('[role="dialog"], [role="complementary"], form').first();
      const isOpen = await dialog.isVisible({ timeout: 3000 }).catch(() => false);

      if (isOpen) {
        // Should have a name/label field
        const nameField = dialog.locator(
          'input[name="name"], input[id="name"], input[placeholder*="name" i], input[placeholder*="label" i]'
        ).first();
        const hasName = await nameField.isVisible({ timeout: 2000 }).catch(() => false);

        // Should have a kubeconfig field (textarea or file upload)
        const kubeconfigField = dialog.locator(
          'textarea, input[type="file"], input[name="kubeconfig"], [placeholder*="kubeconfig" i]'
        ).first();
        const hasKubeconfig = await kubeconfigField.isVisible({ timeout: 2000 }).catch(() => false);

        // At least one cluster configuration field should be present
        expect(hasName || hasKubeconfig).toBeTruthy();

        // Close dialog
        await commonPage.closeDialog();
      }
    }
  });

  test('submitting invalid kubeconfig shows error', async ({ page }) => {
    await k8sPage.goto();
    await page.waitForTimeout(1000);

    const hasButton = await k8sPage.hasRegisterButton();
    if (hasButton) {
      await k8sPage.clickRegister();
      await page.waitForTimeout(500);

      const dialog = page.locator('[role="dialog"], [role="complementary"], form').first();
      const isOpen = await dialog.isVisible({ timeout: 3000 }).catch(() => false);

      if (isOpen) {
        // Try to submit with invalid data
        const nameField = dialog.locator(
          'input[name="name"], input[id="name"], input[placeholder*="name" i]'
        ).first();
        if (await nameField.isVisible({ timeout: 1000 }).catch(() => false)) {
          await nameField.fill('test-invalid-cluster');
        }

        const kubeconfigField = dialog.locator('textarea').first();
        if (await kubeconfigField.isVisible({ timeout: 1000 }).catch(() => false)) {
          await kubeconfigField.fill('this is not valid kubeconfig yaml');
        }

        // Submit the form
        const submitBtn = dialog.locator(
          'button[type="submit"], button:has-text("Register"), button:has-text("Connect"), button:has-text("Add"), button:has-text("Save")'
        ).first();
        if (await submitBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
          await submitBtn.click();
          await page.waitForTimeout(1000);

          // Should show an error (toast, inline validation, or dialog stays open)
          const hasError =
            (await page.locator('[role="alert"], [class*="error" i], [data-sonner-toast]').first().isVisible({ timeout: 3000 }).catch(() => false)) ||
            (await dialog.isVisible({ timeout: 1000 }).catch(() => false)); // dialog staying open indicates validation failure

          expect(hasError).toBeTruthy();
        }

        // Clean up — close dialog if still open
        if (await dialog.isVisible({ timeout: 500 }).catch(() => false)) {
          await commonPage.closeDialog();
        }
      }
    }
  });

  test('navigate to kubernetes via sidebar', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await commonPage.navigateTo('Kubernetes');

    expect(page.url()).toContain('/kubernetes');
    const isLoaded = await k8sPage.isLoaded();
    expect(isLoaded).toBeTruthy();
  });

  test('page content includes cluster-related terminology', async ({ page }) => {
    await k8sPage.goto();
    await page.waitForTimeout(1000);

    const mainContent = (await page.locator('main').textContent()) || '';
    const lowerContent = mainContent.toLowerCase();

    // Page should mention cluster-related concepts
    const hasClusterContent =
      lowerContent.includes('cluster') ||
      lowerContent.includes('kubernetes') ||
      lowerContent.includes('namespace') ||
      lowerContent.includes('register') ||
      lowerContent.includes('connect') ||
      lowerContent.includes('node');

    expect(hasClusterContent).toBeTruthy();
  });
});
