/**
 * Stacks Page Object (E2E-007)
 *
 * Encapsulates interactions with the Blueprints / Stacks page.
 */

import { Page } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

export class StacksPage {
  constructor(private page: Page) {}

  /**
   * Navigate to Stacks / Blueprints page
   */
  async goto() {
    await this.page.goto('/stacks');
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Check if the page has loaded
   */
  async isLoaded(): Promise<boolean> {
    const heading = this.page.locator(
      'h1:has-text("Blueprint"), h1:has-text("Stack"), h2:has-text("Blueprint"), h2:has-text("Stack")'
    ).first();
    return await heading.isVisible({ timeout: getTimeout('shortWait') }).catch(() => false);
  }

  /**
   * Search for a stack/blueprint
   */
  async search(query: string) {
    const searchInput = this.page.locator(
      'input[type="search"], input[placeholder*="search" i], input[placeholder*="filter" i]'
    ).first();
    await searchInput.fill(query);
    await this.page.waitForTimeout(500);
  }

  /**
   * Get count of visible stacks
   */
  async getStackCount(): Promise<number> {
    const items = this.page.locator('[class*="card" i], table tbody tr').filter({
      hasNot: this.page.locator('[class*="empty" i]'),
    });
    return await items.count();
  }

  /**
   * Check if a specific stack is listed
   */
  async isStackVisible(stackName: string): Promise<boolean> {
    const stack = this.page.locator(`text=${stackName}`).first();
    return await stack.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Click on a stack to view details
   */
  async openStack(stackName: string) {
    const stack = this.page.locator(`text=${stackName}`).first();
    await stack.click();
    await this.page.waitForTimeout(1000);
  }

  /**
   * Check if featured section is visible
   */
  async hasFeaturedSection(): Promise<boolean> {
    const featured = this.page.locator('text=/featured|popular|recommended/i').first();
    return await featured.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Check if empty state is shown
   */
  async isEmptyState(): Promise<boolean> {
    const empty = this.page.locator('text=/no blueprint|no stack|empty|not found/i').first();
    return await empty.isVisible({ timeout: 3000 }).catch(() => false);
  }
}
