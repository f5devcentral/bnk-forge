/**
 * Dashboard Page Object (E2E-002)
 *
 * Encapsulates interactions with the Command Center / Dashboard page.
 */

import { Page } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

export class DashboardPage {
  constructor(private page: Page) {}

  /**
   * Navigate to Dashboard
   */
  async goto() {
    await this.page.goto('/');
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Check if dashboard has loaded (look for key indicators)
   */
  async isLoaded(): Promise<boolean> {
    // Dashboard should show "Command Center" or similar heading
    const heading = this.page.locator(
      'h1:has-text("Command Center"), h1:has-text("Dashboard"), h2:has-text("Command Center")'
    );
    return await heading.isVisible({ timeout: getTimeout('shortWait') }).catch(() => false);
  }

  /**
   * Get the page title/heading text
   */
  async getHeading(): Promise<string> {
    const heading = this.page.locator('h1, h2').first();
    return (await heading.textContent()) || '';
  }

  /**
   * Check if project summary widget is visible
   */
  async hasProjectSummary(): Promise<boolean> {
    const widget = this.page.locator('text=/projects?/i').first();
    return await widget.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Check if recent activity section is visible
   */
  async hasRecentActivity(): Promise<boolean> {
    const section = this.page.locator('text=/recent|activity|tasks/i').first();
    return await section.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Check if system health/status section is visible
   */
  async hasSystemHealth(): Promise<boolean> {
    const section = this.page.locator('text=/health|status|system/i').first();
    return await section.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Get count of dashboard cards/widgets
   */
  async getWidgetCount(): Promise<number> {
    // Dashboard typically uses Card components
    const cards = this.page.locator('main [class*="card"], main [class*="Card"]');
    return await cards.count();
  }
}
