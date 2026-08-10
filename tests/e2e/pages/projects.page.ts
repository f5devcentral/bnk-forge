/**
 * Projects Page Object
 *
 * Encapsulates interactions with the Projects page.
 */

import { Page, expect } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

export class ProjectsPage {
  constructor(private page: Page) {}

  /**
   * Navigate to Projects page
   */
  async goto() {
    await this.page.goto(`${TEST_CONFIG.baseUrl}/projects`);
    await this.page.waitForLoadState('networkidle');

    // Dismiss onboarding dialog if it appears
    await this.dismissOnboardingIfPresent();
  }

  /**
   * Dismiss onboarding/welcome dialog if present
   */
  async dismissOnboardingIfPresent() {
    try {
      // Wait briefly for the dialog to appear
      const dialog = this.page.locator('[role="dialog"]');

      // Keep clicking Next/Continue buttons until they're gone
      for (let i = 0; i < 10; i++) {
        const nextButton = this.page.locator('button:has-text("Next"), button:has-text("Continue")');

        if (await nextButton.isVisible({ timeout: 500 }).catch(() => false)) {
          await nextButton.first().click();
          await this.page.waitForTimeout(300);
        } else {
          break;
        }
      }

      // Click final button to close dialog
      const finalButtons = [
        'button:has-text("Get Started")',
        'button:has-text("Done")',
        'button:has-text("Close")',
        'button:has-text("Finish")'
      ];

      for (const selector of finalButtons) {
        if (await this.page.locator(selector).isVisible({ timeout: 500 }).catch(() => false)) {
          await this.page.locator(selector).click();
          await this.page.waitForTimeout(500);
          break;
        }
      }
    } catch (error) {
      // If dialog doesn't appear or dismissal fails, continue anyway
    }
  }

  /**
   * Click "New Project" button and wait for dialog
   * Note: There are multiple "New Project" buttons on the page,
   * so we target the main one in the page header or the "Create Your First Project" button
   */
  async clickCreateProject() {
    // Try main "New Project" button first (in header)
    const mainButton = this.page.locator('main button:has-text("New Project")');
    const firstProjectButton = this.page.locator('button:has-text("Create Your First Project")');

    if (await mainButton.isVisible({ timeout: 1000 }).catch(() => false)) {
      await mainButton.click();
    } else if (await firstProjectButton.isVisible({ timeout: 1000 }).catch(() => false)) {
      await firstProjectButton.click();
    } else {
      // Fallback to any visible New Project button
      await this.page.locator('button:has-text("New Project")').first().click();
    }

    // Wait for dialog to appear
    await this.page.waitForSelector('[role="dialog"]', {
      timeout: getTimeout('shortWait'),
    });
  }

  /**
   * Fill in project creation form
   */
  async fillProjectForm(data: {
    name: string;
    description: string;
    awsRegion?: string;
  }) {
    // Wait for dialog to be ready
    await this.page.waitForSelector('input[id="name"]', { timeout: getTimeout('shortWait') });

    // Fill project name
    await this.page.fill('input[id="name"]', data.name);

    // Fill description
    await this.page.fill('textarea[id="description"]', data.description);

    // Note: AWS region is set at project level in settings, not in create dialog
  }

  /**
   * Submit project creation form
   * NOTE: Submit button text is "Create Project"
   */
  async submitProjectForm() {
    await this.page.click('button[type="submit"]:has-text("Create Project")');

    // Wait for dialog to close
    await this.page.waitForSelector('[role="dialog"]', {
      state: 'hidden',
      timeout: getTimeout('mediumWait'),
    });
  }

  /**
   * Create a new project (complete flow)
   */
  async createProject(data: {
    name: string;
    description: string;
    awsRegion: string;
  }) {
    await this.clickCreateProject();
    await this.fillProjectForm(data);
    await this.submitProjectForm();

    // Wait for project card to appear
    await this.page.waitForSelector(`text=${data.name}`, {
      timeout: getTimeout('mediumWait'),
    });
  }

  /**
   * Find project card by name
   */
  async findProjectCard(projectName: string) {
    return this.page.locator(`[data-testid="project-card"]:has-text("${projectName}")`).first();
  }

  /**
   * Click on project card to navigate to details
   */
  async openProject(projectName: string) {
    const card = await this.findProjectCard(projectName);

    // Wait for card to be visible before clicking
    await card.waitFor({ state: 'visible', timeout: getTimeout('mediumWait') });
    await card.click();

    // Wait for project detail page to load
    await this.page.waitForURL(/\/projects\/\d+/, {
      timeout: getTimeout('navigation'),
    });

    // Wait for page to be fully loaded
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Verify project exists in list
   */
  async verifyProjectExists(projectName: string): Promise<boolean> {
    const card = await this.findProjectCard(projectName);
    return (await card.count()) > 0;
  }

  /**
   * Delete project
   * NOTE: Project cards no longer have 3-dot menus (Phase 3 changes)
   * Must navigate to project detail page to access delete button
   */
  async deleteProject(projectName: string) {
    // Navigate to project detail page
    await this.openProject(projectName);

    // Wait for detail page to load
    await this.page.waitForURL(/\/projects\/\d+/, {
      timeout: getTimeout('navigation'),
    });
    await this.page.waitForLoadState('networkidle');

    // Click Delete button in header
    const deleteButton = this.page.locator('button:has-text("Delete")').first();
    await deleteButton.waitFor({ state: 'visible', timeout: getTimeout('shortWait') });
    await deleteButton.click();

    // Wait for confirmation dialog
    await this.page.waitForTimeout(500);

    // Confirm deletion - button text is "Delete" in AlertDialog
    const confirmButton = this.page.locator('[role="alertdialog"] button:has-text("Delete"), button:has-text("Confirm")').first();
    await confirmButton.waitFor({ state: 'visible', timeout: getTimeout('shortWait') });
    await confirmButton.click();

    // Wait for navigation back to projects page
    await this.page.waitForURL(/\/projects$/, {
      timeout: getTimeout('navigation'),
    });
  }
}
