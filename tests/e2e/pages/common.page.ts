/**
 * Common Page Object (E2E-003)
 *
 * Shared utilities for sidebar navigation, toasts, modals, and common UI patterns.
 * Used by all other page objects and specs.
 */

import { Page, expect } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

/** Sidebar navigation items grouped by section */
export const SIDEBAR_NAV = {
  observe: [{ label: 'Command Center', path: '/' }],
  build: [
    { label: 'Blueprints', path: '/stacks' },
    { label: 'Projects', path: '/projects' },
    { label: 'Module Catalog', path: '/modules' },
    { label: 'Helm Packages', path: '/helm' },
  ],
  operate: [
    { label: 'Activity', path: '/tasks' },
    { label: 'Kubernetes', path: '/kubernetes' },
    { label: 'F5 BNK', path: '/bnk' },
    { label: 'Fleet', path: '/fleet' },
    { label: 'Operators', path: '/operators' },
  ],
  settings: [
    { label: 'Auth Templates', path: '/auth-templates', minRole: 'operator' },
    { label: 'System', path: '/system', minRole: 'admin' },
  ],
} as const;

export class CommonPage {
  constructor(private page: Page) {}

  // ─── Sidebar Navigation ─────────────────────────────────────────────────────

  /**
   * Get the sidebar element
   */
  getSidebar() {
    return this.page.locator('aside[aria-label="Application sidebar"]');
  }

  /**
   * Get main navigation
   */
  getNav() {
    return this.page.locator('nav[aria-label="Main navigation"]');
  }

  /**
   * Click a sidebar navigation link by label
   */
  async navigateTo(label: string) {
    const link = this.getNav().locator(`a:has-text("${label}")`);
    await link.click();
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Check if a sidebar link is visible
   */
  async isSidebarLinkVisible(label: string): Promise<boolean> {
    const link = this.getNav().locator(`a:has-text("${label}")`);
    return await link.isVisible({ timeout: 2000 }).catch(() => false);
  }

  /**
   * Get all visible sidebar link labels
   */
  async getVisibleSidebarLinks(): Promise<string[]> {
    const links = this.getNav().locator('a');
    const count = await links.count();
    const labels: string[] = [];
    for (let i = 0; i < count; i++) {
      const text = await links.nth(i).textContent();
      if (text?.trim()) labels.push(text.trim());
    }
    return labels;
  }

  /**
   * Check if sidebar section heading is visible
   */
  async isSidebarSectionVisible(sectionName: string): Promise<boolean> {
    const section = this.getSidebar().locator(`text="${sectionName}"`);
    return await section.isVisible({ timeout: 2000 }).catch(() => false);
  }

  /**
   * Collapse the sidebar
   */
  async collapseSidebar() {
    const collapseBtn = this.page.locator('button[aria-label="Collapse sidebar"]');
    if (await collapseBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await collapseBtn.click();
    }
  }

  /**
   * Expand the sidebar
   */
  async expandSidebar() {
    const expandBtn = this.page.locator('button[aria-label="Expand sidebar"]');
    if (await expandBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await expandBtn.click();
    }
  }

  /**
   * Get the logged-in username from sidebar footer
   */
  async getLoggedInUsername(): Promise<string | null> {
    // Sidebar footer shows username
    const sidebar = this.getSidebar();
    const usernameEl = sidebar.locator('text=/admin|operator|viewer/i').first();
    if (await usernameEl.isVisible({ timeout: 2000 }).catch(() => false)) {
      return await usernameEl.textContent();
    }
    return null;
  }

  /**
   * Click the logout button in sidebar footer
   */
  async logout() {
    const logoutBtn = this.page.locator('button[aria-label="Logout"]');
    await logoutBtn.click();
    await this.page.waitForURL('/login', { timeout: getTimeout('navigation') });
  }

  // ─── Header / Breadcrumbs ────────────────────────────────────────────────────

  /**
   * Get breadcrumb text
   */
  async getBreadcrumbText(): Promise<string> {
    const breadcrumb = this.page.locator('nav[aria-label="Breadcrumb"], [class*="breadcrumb" i]');
    if (await breadcrumb.isVisible({ timeout: 2000 }).catch(() => false)) {
      return (await breadcrumb.textContent()) || '';
    }
    return '';
  }

  // ─── Toast Notifications ─────────────────────────────────────────────────────

  /**
   * Wait for a bell notification to appear and get its text.
   * TODO(D-027): repoint to bell — old Sonner [data-sonner-toast] selector removed.
   * Use the notification-centre popover once E2E flows are updated.
   */
  async waitForToast(timeout: number = 5000): Promise<string> {
    // TODO(D-027): repoint to bell notification centre — Sonner removed
    // Placeholder: wait for the unread badge on the bell button to appear
    const badge = this.page.locator('button[aria-label="Notifications"] .badge, button[title="Notifications"] span').first();
    await badge.waitFor({ state: 'visible', timeout }).catch(() => {});
    return '';
  }

  /**
   * Check if a bell notification with specific text appeared.
   * TODO(D-027): repoint to bell — old Sonner [data-sonner-toast] selector removed.
   */
  async hasToast(_text: string, _timeout: number = 5000): Promise<boolean> {
    // TODO(D-027): repoint to bell notification centre — Sonner removed
    return false;
  }

  /**
   * Dismiss all bell notifications.
   * TODO(D-027): repoint to bell — old Sonner [data-sonner-toast] selector removed.
   */
  async dismissToasts() {
    // TODO(D-027): repoint to bell notification centre — Sonner removed
  }

  // ─── Dialogs / Modals ────────────────────────────────────────────────────────

  /**
   * Wait for a dialog to open
   */
  async waitForDialog(timeout?: number) {
    await this.page.waitForSelector('[role="dialog"]', {
      timeout: timeout || getTimeout('shortWait'),
    });
  }

  /**
   * Wait for dialog to close
   */
  async waitForDialogClose(timeout?: number) {
    await this.page.waitForSelector('[role="dialog"]', {
      state: 'hidden',
      timeout: timeout || getTimeout('shortWait'),
    });
  }

  /**
   * Check if any dialog is open
   */
  async isDialogOpen(): Promise<boolean> {
    return await this.page.locator('[role="dialog"]').isVisible({ timeout: 1000 }).catch(() => false);
  }

  /**
   * Get dialog title text
   */
  async getDialogTitle(): Promise<string> {
    const title = this.page.locator('[role="dialog"] h2, [role="dialog"] [class*="title" i]').first();
    return (await title.textContent()) || '';
  }

  /**
   * Close dialog by clicking X button or Cancel
   */
  async closeDialog() {
    // Try close/X button first, then Cancel
    const closeBtn = this.page.locator(
      '[role="dialog"] button[aria-label="Close"], [role="dialog"] button:has-text("Cancel")'
    ).first();
    if (await closeBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await closeBtn.click();
      await this.waitForDialogClose();
    }
  }

  // ─── Command Palette ─────────────────────────────────────────────────────────

  /**
   * Open command palette with Cmd+K
   */
  async openCommandPalette() {
    await this.page.keyboard.press('Meta+k');
    await this.page.waitForTimeout(500);
  }

  // ─── Assertions ──────────────────────────────────────────────────────────────

  /**
   * Assert current URL path
   */
  async expectPath(path: string) {
    await expect(this.page).toHaveURL(new RegExp(`${path}$`));
  }

  /**
   * Assert page has no console errors (call at end of test)
   */
  async expectNoConsoleErrors(errors: string[]) {
    const criticalErrors = errors.filter(
      (e) => !e.includes('favicon') && !e.includes('websocket') && !e.includes('WebSocket')
    );
    expect(criticalErrors).toHaveLength(0);
  }
}
