/**
 * Helm Page Object (E2E-017)
 *
 * Encapsulates interactions with the Helm Packages page.
 */

import { Page } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

export class HelmPage {
  constructor(private page: Page) {}

  /**
   * Navigate to Helm page
   */
  async goto() {
    await this.page.goto('/helm');
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Check if the page has loaded
   */
  async isLoaded(): Promise<boolean> {
    const heading = this.page.locator(
      'h1:has-text("Helm"), h2:has-text("Helm"), h1:has-text("Package"), h2:has-text("Package")'
    ).first();
    return await heading.isVisible({ timeout: getTimeout('shortWait') }).catch(() => false);
  }

  /**
   * Check if empty state (no releases) is shown
   */
  async isEmptyState(): Promise<boolean> {
    const empty = this.page.locator('text=/no helm|no release|no chart|empty|no package/i').first();
    return await empty.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Check if releases are listed
   */
  async hasReleases(): Promise<boolean> {
    const releases = this.page.locator('text=/release|installed|deployed/i').first();
    return await releases.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Get the count of visible release rows/cards
   */
  async getReleaseCount(): Promise<number> {
    const items = this.page.locator('table tbody tr, [class*="card" i]').filter({
      hasNot: this.page.locator('[class*="empty" i]'),
    });
    return await items.count();
  }

  /**
   * Check if "Manage Repositories" or similar action button is present
   */
  async hasManageReposButton(): Promise<boolean> {
    const btn = this.page.locator(
      'button:has-text("Manage"), button:has-text("Repository"), button:has-text("Repos")'
    ).first();
    return await btn.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Click the manage repositories button
   */
  async clickManageRepos() {
    const btn = this.page.locator(
      'button:has-text("Manage"), button:has-text("Repository"), button:has-text("Repos")'
    ).first();
    await btn.click();
    await this.page.waitForTimeout(500);
  }

  /**
   * Check if "Install Chart" or "Add" button is present
   */
  async hasInstallButton(): Promise<boolean> {
    const btn = this.page.locator(
      'button:has-text("Install"), button:has-text("Add"), button:has-text("Deploy")'
    ).first();
    return await btn.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Click install chart button
   */
  async clickInstall() {
    const btn = this.page.locator(
      'button:has-text("Install"), button:has-text("Add"), button:has-text("Deploy")'
    ).first();
    await btn.click();
    await this.page.waitForTimeout(500);
  }

  /**
   * Check if cluster selector is visible (Helm requires a cluster context)
   */
  async hasClusterSelector(): Promise<boolean> {
    const selector = this.page.locator(
      'select:has-text("Cluster"), [role="combobox"]:has-text("Cluster"), button:has-text("Select Cluster")'
    ).first();
    return await selector.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Search for a chart or release
   */
  async search(query: string) {
    const searchInput = this.page.locator(
      'input[type="search"], input[placeholder*="search" i], input[placeholder*="filter" i]'
    ).first();
    if (await searchInput.isVisible({ timeout: 2000 }).catch(() => false)) {
      await searchInput.fill(query);
      await this.page.waitForTimeout(500);
    }
  }

  /**
   * Get the main content text of the helm page
   */
  async getMainContent(): Promise<string> {
    return (await this.page.locator('main').textContent()) || '';
  }
}
