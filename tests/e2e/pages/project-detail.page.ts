/**
 * Project Detail Page Object
 *
 * Encapsulates interactions with the Project Detail page.
 */

import { Page, Locator } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

export class ProjectDetailPage {
  constructor(private page: Page) {}

  /**
   * Navigate to project detail page by ID
   */
  async goto(projectId: number) {
    await this.page.goto(`${TEST_CONFIG.baseUrl}/projects/${projectId}`);
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Click "Add Module" button
   * Note: There may be multiple "Add Module" buttons, use first visible one
   */
  async clickAddModule() {
    // Wait for the Add Module button to be visible and click the first one
    const addModuleButton = this.page.locator('button:has-text("Add Module")').first();
    await addModuleButton.waitFor({ state: 'visible', timeout: getTimeout('mediumWait') });
    await addModuleButton.click();

    // Wait for module selection dialog/sheet
    await this.page.waitForSelector('[role="dialog"], [role="complementary"]', {
      timeout: getTimeout('mediumWait'),
    });
  }

  /**
   * Search for module in add module dialog
   */
  async searchModule(moduleName: string) {
    // Look for search input in dialog
    const searchInput = this.page.locator('[role="dialog"] input[type="search"], [role="dialog"] input[placeholder*="search" i]');
    await searchInput.fill(moduleName);

    // Wait a moment for search results
    await this.page.waitForTimeout(1000);
  }

  /**
   * Select module from library
   */
  async selectModuleFromLibrary(modulePath: string) {
    // Click on module card that contains the path
    await this.page.click(`[data-testid="module-card"]:has-text("${modulePath}"), text=${modulePath}`);
  }

  /**
   * Add module with path (complete flow)
   * NOTE: This is a multi-step wizard flow:
   * 1. Select module from library
   * 2. Configure variables
   * 3. Confirm and add
   */
  async addModule(modulePath: string, variables?: Record<string, string>) {
    await this.clickAddModule();

    // Wait for module library dialog to load
    await this.page.waitForTimeout(2000);

    // Extract module name from path for searching (e.g., "aws/vpc" -> "vpc")
    const moduleName = modulePath.split('/').pop() || modulePath;

    // Search for the module first
    const searchInput = this.page.locator('input[type="search"], input[placeholder*="search" i]').first();
    if (await searchInput.isVisible({ timeout: 2000 }).catch(() => false)) {
      await searchInput.fill(moduleName);
      await this.page.waitForTimeout(1000); // Wait for search to filter
    }

    // Find module button by matching the module name specifically
    // The button structure is: button > div.font-medium containing name
    // We need to be specific to avoid matching descriptions that contain the search term
    const moduleCard = this.page.locator(`[role="dialog"] button`).filter({
      has: this.page.locator('div.font-medium').filter({ hasText: new RegExp(`^${moduleName}$`, 'i') })
    }).first();

    // Click the module card
    await moduleCard.waitFor({ state: 'visible', timeout: getTimeout('mediumWait') });
    await moduleCard.click();

    // Wait for Next/Continue button and click it using JavaScript
    await this.page.waitForTimeout(1000); // Give dialog time to update

    // Find and click the Next button using JavaScript to avoid viewport issues
    const clicked = await this.page.evaluate(() => {
      const dialog = document.querySelector('[role="dialog"]');
      if (!dialog) return false;

      // Find Next or Continue button
      const buttons = Array.from(dialog.querySelectorAll('button'));
      const nextButton = buttons.find(btn =>
        btn.textContent?.trim() === 'Next' || btn.textContent?.trim() === 'Continue'
      );

      if (nextButton && nextButton instanceof HTMLElement) {
        nextButton.click();
        return true;
      }
      return false;
    });

    if (clicked) {
      await this.page.waitForTimeout(1000);
    }

    // If variables provided, fill them
    if (variables && Object.keys(variables).length > 0) {
      await this.fillModuleVariables(variables);
    }

    // Click final Add/Confirm button
    const addButton = this.page.locator('button:has-text("Add Module"), button:has-text("Add to Project"), button:has-text("Confirm")').first();
    await addButton.waitFor({ state: 'visible', timeout: getTimeout('shortWait') });
    await addButton.click();

    // Wait for dialog to close and module to appear in project
    await this.page.waitForTimeout(2000);
  }

  /**
   * Fill module variables in configuration dialog
   */
  async fillModuleVariables(variables: Record<string, string>) {
    for (const [key, value] of Object.entries(variables)) {
      // Find input by label or name
      const input = this.page.locator(`input[name="${key}"], input[id="${key}"]`);
      if (await input.count() > 0) {
        await input.fill(value);
      }
    }

    // Submit form
    await this.page.click('button[type="submit"]:has-text("Save"), button:has-text("Add Module")');
  }

  /**
   * Find module card/row by name/path
   * Uses data-testid="module-card" on table rows
   */
  getModuleCard(moduleName: string): Locator {
    return this.page.locator(`[data-testid="module-card"]:has-text("${moduleName}")`).first();
  }

  /**
   * Get module status badge text
   * Uses data-testid="module-status" on the status badge
   */
  async getModuleStatus(moduleName: string): Promise<string> {
    const card = this.getModuleCard(moduleName);
    const statusBadge = card.locator('[data-testid="module-status"]').first();
    return await statusBadge.textContent() || '';
  }

  /**
   * Open the actions dropdown menu for a module row
   * All actions (Init, Plan, Apply, Destroy) are inside a dropdown menu
   */
  private async openModuleActionsMenu(moduleName: string) {
    const card = this.getModuleCard(moduleName);
    await card.waitFor({ state: 'visible', timeout: getTimeout('mediumWait') });

    // Click the 3-dot menu button (MoreVertical icon)
    const menuButton = card.locator('[data-testid="module-actions-menu"], button[aria-label="Module actions"]');
    await menuButton.waitFor({ state: 'visible', timeout: getTimeout('shortWait') });
    await menuButton.click();

    // Wait for dropdown menu to open
    await this.page.waitForTimeout(500);
  }

  /**
   * Click Initialize option from module dropdown menu
   * NOTE: Menu item text is "Initialize" not "Init"
   */
  async initModule(moduleName: string) {
    await this.openModuleActionsMenu(moduleName);

    // Click Initialize in dropdown
    const initItem = this.page.locator('[role="menuitem"]:has-text("Initialize")');
    await initItem.waitFor({ state: 'visible', timeout: getTimeout('shortWait') });
    await initItem.click();

    // Wait for task to queue
    await this.page.waitForTimeout(2000);
  }

  /**
   * Click Plan option from module dropdown menu
   */
  async planModule(moduleName: string) {
    await this.openModuleActionsMenu(moduleName);

    // Click Plan in dropdown
    const planItem = this.page.locator('[role="menuitem"]:has-text("Plan")');
    await planItem.waitFor({ state: 'visible', timeout: getTimeout('shortWait') });
    await planItem.click();

    // Wait for task to queue
    await this.page.waitForTimeout(2000);
  }

  /**
   * Click Apply option from module dropdown menu
   * NOTE: May show confirmation dialog with "Apply with Auto-Approve"
   */
  async applyModule(moduleName: string) {
    await this.openModuleActionsMenu(moduleName);

    // Click Apply in dropdown
    const applyItem = this.page.locator('[role="menuitem"]:has-text("Apply")');
    await applyItem.waitFor({ state: 'visible', timeout: getTimeout('shortWait') });
    await applyItem.click();

    // Wait for confirmation dialog to appear
    await this.page.waitForTimeout(1000);

    // Confirm in dialog - button text is "Apply with Auto-Approve" or just "Apply"
    const confirmButton = this.page.locator('button:has-text("Apply with Auto-Approve"), [role="alertdialog"] button:has-text("Apply")').first();
    if (await confirmButton.isVisible({ timeout: 2000 }).catch(() => false)) {
      await confirmButton.click();
    }

    // Wait for task to queue
    await this.page.waitForTimeout(2000);
  }

  /**
   * Click Destroy option from module dropdown menu
   */
  async destroyModule(moduleName: string) {
    await this.openModuleActionsMenu(moduleName);

    // Click Destroy in dropdown
    const destroyItem = this.page.locator('[role="menuitem"]:has-text("Destroy")');
    await destroyItem.waitFor({ state: 'visible', timeout: getTimeout('shortWait') });
    await destroyItem.click();

    // Wait for confirmation dialog
    await this.page.waitForTimeout(500);

    // Confirm in dialog - button text is "Destroy Infrastructure" or just confirm
    const confirmButton = this.page.locator('button:has-text("Destroy Infrastructure"), [role="alertdialog"] button:has-text("Destroy"), button:has-text("Confirm")').first();
    await confirmButton.waitFor({ state: 'visible', timeout: getTimeout('shortWait') });
    await confirmButton.click();

    // Wait for task to queue
    await this.page.waitForTimeout(2000);
  }

  /**
   * Wait for module to reach specific status
   */
  async waitForModuleStatus(
    moduleName: string,
    expectedStatus: string,
    timeoutMs: number = 120000
  ): Promise<boolean> {
    const startTime = Date.now();
    const pollInterval = 3000;

    while (Date.now() - startTime < timeoutMs) {
      try {
        // Get the module card and check if status badge exists
        const card = this.getModuleCard(moduleName);
        await card.waitFor({ state: 'visible', timeout: 5000 });

        const status = await this.getModuleStatus(moduleName);

        if (status.toLowerCase().includes(expectedStatus.toLowerCase())) {
          return true;
        }

        // For terminal states (completed/failed), break early
        if (['failed', 'error', 'deployed', 'applied'].some(s => status.toLowerCase().includes(s))) {
          // If we're looking for these statuses, we found them
          if (expectedStatus.toLowerCase().includes('fail') ||
              expectedStatus.toLowerCase().includes('error') ||
              expectedStatus.toLowerCase().includes('deploy') ||
              expectedStatus.toLowerCase().includes('applied')) {
            return status.toLowerCase().includes(expectedStatus.toLowerCase());
          }
          // Otherwise, we've reached a terminal state that doesn't match
          return false;
        }

        // Refresh page to get latest status
        await this.page.reload({ waitUntil: 'networkidle' });

        // Wait before next check
        await this.page.waitForTimeout(pollInterval);
      } catch (error) {
        // If we can't find the module, wait and retry
        await this.page.waitForTimeout(pollInterval);
      }
    }

    return false;
  }

  /**
   * Check if dependency status shows ready
   */
  async areDependenciesReady(moduleName: string): Promise<boolean> {
    const card = this.getModuleCard(moduleName);

    // Look for dependency status indicators
    const dependencySection = card.locator('[data-testid="dependency-status"], .dependency-status, :has-text("Dependencies")');

    if (await dependencySection.count() === 0) {
      // No dependencies section means no dependencies needed
      return true;
    }

    // Check for "Ready" or success indicators
    const readyIndicator = dependencySection.locator('text=/ready|deployed/i, svg[class*="check"], [class*="success"]');
    return (await readyIndicator.count()) > 0;
  }

  /**
   * Get module outputs
   */
  async getModuleOutputs(moduleName: string): Promise<Record<string, any>> {
    const card = this.getModuleCard(moduleName);

    // Click to view outputs (from 3-dot menu or dedicated button)
    await card.locator('button:has-text("Outputs"), button[aria-label*="menu" i]').first().click();

    // If menu, click outputs option
    const outputsMenuItem = this.page.locator('text=/view outputs|outputs/i');
    if (await outputsMenuItem.count() > 0) {
      await outputsMenuItem.click();
    }

    // Wait for outputs dialog/panel
    await this.page.waitForTimeout(2000);

    // Parse outputs from display (implementation depends on UI structure)
    // For now, return empty object - can be enhanced based on actual UI
    return {};
  }

  /**
   * Navigate to Tasks page
   */
  async gotoTasks() {
    await this.page.click('a[href="/tasks"], nav a:has-text("Tasks")');
    await this.page.waitForURL(/\/tasks/, {
      timeout: getTimeout('navigation'),
    });
  }
}
