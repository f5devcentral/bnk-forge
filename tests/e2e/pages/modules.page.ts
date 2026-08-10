/**
 * Modules Page Object (E2E-006)
 *
 * Encapsulates interactions with the Module Catalog page.
 */

import { Page } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

export class ModulesPage {
  constructor(private page: Page) {}

  /**
   * Navigate to Modules page
   */
  async goto() {
    await this.page.goto('/modules');
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Check if the page has loaded
   */
  async isLoaded(): Promise<boolean> {
    const heading = this.page.locator('h1:has-text("Module"), h2:has-text("Module")').first();
    return await heading.isVisible({ timeout: getTimeout('shortWait') }).catch(() => false);
  }

  /**
   * Search for a module by name
   */
  async searchModule(query: string) {
    const searchInput = this.page.locator(
      'input[type="search"], input[placeholder*="search" i], input[placeholder*="filter" i]'
    ).first();
    await searchInput.fill(query);
    await this.page.waitForTimeout(500); // Wait for filter to apply
  }

  /**
   * Clear search field
   */
  async clearSearch() {
    const searchInput = this.page.locator(
      'input[type="search"], input[placeholder*="search" i], input[placeholder*="filter" i]'
    ).first();
    await searchInput.clear();
    await this.page.waitForTimeout(500);
  }

  /**
   * Get all visible module cards/rows
   */
  getModuleItems() {
    return this.page.locator('[class*="card" i], table tbody tr, [role="row"]');
  }

  /**
   * Get count of visible modules
   */
  async getModuleCount(): Promise<number> {
    return await this.getModuleItems().count();
  }

  /**
   * Check if a specific module is listed
   */
  async isModuleVisible(moduleName: string): Promise<boolean> {
    const module = this.page.locator(`text=${moduleName}`).first();
    return await module.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Click on a module to view details
   */
  async openModule(moduleName: string) {
    const module = this.page.locator(`text=${moduleName}`).first();
    await module.click();
    await this.page.waitForTimeout(1000);
  }

  /**
   * Check if empty state is shown
   */
  async isEmptyState(): Promise<boolean> {
    const empty = this.page.locator('text=/no modules|empty|not found/i').first();
    return await empty.isVisible({ timeout: 3000 }).catch(() => false);
  }
}
