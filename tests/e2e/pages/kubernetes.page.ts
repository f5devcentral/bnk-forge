/**
 * Kubernetes Page Object (E2E-008)
 *
 * Encapsulates interactions with the Kubernetes Explorer page.
 */

import { Page } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

export class KubernetesPage {
  constructor(private page: Page) {}

  /**
   * Navigate to Kubernetes page
   */
  async goto() {
    await this.page.goto('/kubernetes');
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Check if the page has loaded
   */
  async isLoaded(): Promise<boolean> {
    const heading = this.page.locator(
      'h1:has-text("Kubernetes"), h2:has-text("Kubernetes"), h1:has-text("Cluster")'
    ).first();
    return await heading.isVisible({ timeout: getTimeout('shortWait') }).catch(() => false);
  }

  /**
   * Check if empty state (no clusters) is shown
   */
  async isEmptyState(): Promise<boolean> {
    const empty = this.page.locator('text=/no cluster|no kubernetes|connect|register|add cluster/i').first();
    return await empty.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Check if cluster list is visible
   */
  async hasClusterList(): Promise<boolean> {
    const clusters = this.page.locator('text=/cluster/i').first();
    return await clusters.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Check if register/connect dialog trigger is visible
   */
  async hasRegisterButton(): Promise<boolean> {
    const btn = this.page.locator(
      'button:has-text("Register"), button:has-text("Connect"), button:has-text("Add Cluster")'
    ).first();
    return await btn.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Click register/connect cluster button
   */
  async clickRegister() {
    const btn = this.page.locator(
      'button:has-text("Register"), button:has-text("Connect"), button:has-text("Add Cluster")'
    ).first();
    await btn.click();
    await this.page.waitForTimeout(500);
  }

  /**
   * Check if loading state is displayed
   */
  async isLoading(): Promise<boolean> {
    const loading = this.page.locator('[class*="skeleton"], [class*="loading" i], [class*="spinner" i]').first();
    return await loading.isVisible({ timeout: 2000 }).catch(() => false);
  }
}
