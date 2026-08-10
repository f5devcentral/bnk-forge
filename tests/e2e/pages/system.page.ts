/**
 * System Page Object (E2E-009)
 *
 * Encapsulates interactions with the System Administration page.
 */

import { Page } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

export class SystemPage {
  constructor(private page: Page) {}

  /**
   * Navigate to System page
   */
  async goto() {
    await this.page.goto('/system');
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Check if the page has loaded
   */
  async isLoaded(): Promise<boolean> {
    const heading = this.page.locator(
      'h1:has-text("System"), h2:has-text("System"), h1:has-text("Administration")'
    ).first();
    return await heading.isVisible({ timeout: getTimeout('shortWait') }).catch(() => false);
  }

  /**
   * Get visible tab names
   */
  async getTabNames(): Promise<string[]> {
    const tabs = this.page.locator('[role="tablist"] [role="tab"]');
    const count = await tabs.count();
    const names: string[] = [];
    for (let i = 0; i < count; i++) {
      const text = await tabs.nth(i).textContent();
      if (text?.trim()) names.push(text.trim());
    }
    return names;
  }

  /**
   * Click a tab by name
   */
  async clickTab(tabName: string) {
    const tab = this.page.locator(`[role="tab"]:has-text("${tabName}")`);
    await tab.click();
    await this.page.waitForTimeout(500);
  }

  /**
   * Check if Users tab content is visible
   */
  async hasUsersContent(): Promise<boolean> {
    const users = this.page.locator('text=/users|username|role/i').first();
    return await users.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Check if health/monitor section is visible
   */
  async hasHealthSection(): Promise<boolean> {
    const health = this.page.locator('text=/health|monitor|status|uptime/i').first();
    return await health.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Click create user button
   */
  async clickCreateUser() {
    const btn = this.page.locator(
      'button:has-text("Create User"), button:has-text("Add User"), button:has-text("New User")'
    ).first();
    await btn.click();
    await this.page.waitForTimeout(500);
  }

  /**
   * Fill create user form
   */
  async fillUserForm(data: { username: string; email: string; password: string; role?: string }) {
    await this.page.fill('input[name="username"], input[id="username"]', data.username);
    await this.page.fill('input[name="email"], input[id="email"]', data.email);
    await this.page.fill('input[name="password"], input[id="password"]', data.password);

    if (data.role) {
      // Select role from dropdown
      const roleSelect = this.page.locator('select[name="role"], [id="role"]');
      if (await roleSelect.isVisible({ timeout: 1000 }).catch(() => false)) {
        await roleSelect.selectOption(data.role);
      } else {
        // May be a custom select component — click and choose
        const roleTrigger = this.page.locator('button:has-text("Role"), button:has-text("Select role")').first();
        if (await roleTrigger.isVisible({ timeout: 1000 }).catch(() => false)) {
          await roleTrigger.click();
          await this.page.locator(`[role="option"]:has-text("${data.role}")`).click();
        }
      }
    }
  }

  /**
   * Submit user form
   */
  async submitUserForm() {
    await this.page.click('button[type="submit"]');
    await this.page.waitForTimeout(1000);
  }

  /**
   * Check if audit log section is visible
   */
  async hasAuditLog(): Promise<boolean> {
    const audit = this.page.locator('text=/audit|log|event/i').first();
    return await audit.isVisible({ timeout: 3000 }).catch(() => false);
  }

  /**
   * Navigate to system page with specific tab via URL param
   */
  async gotoTab(tab: string) {
    await this.page.goto(`/system?tab=${tab}`);
    await this.page.waitForLoadState('networkidle');
  }
}
