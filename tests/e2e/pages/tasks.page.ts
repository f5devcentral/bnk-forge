/**
 * Tasks Page Object
 *
 * Encapsulates interactions with the Tasks/History page.
 */

import { Page, Locator } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

export class TasksPage {
  constructor(private page: Page) {}

  /**
   * Navigate to Tasks page
   */
  async goto() {
    await this.page.goto(`${TEST_CONFIG.baseUrl}/tasks`);
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Get all task rows
   */
  getTaskRows(): Locator {
    return this.page.locator('[data-testid="task-row"]');
  }

  /**
   * Find task by module name and operation
   * Task rows show: type badge (Initialize/Plan/Apply/Destroy) + module name in project/module column
   * The type badge uses display labels: "Initialize", "Plan", "Apply", "Destroy"
   */
  findTask(moduleName: string, operation: 'init' | 'plan' | 'apply' | 'destroy'): Locator {
    // Map operation to display label used in the type badge
    const typeLabels: Record<string, string> = {
      init: 'Initialize',
      plan: 'Plan',
      apply: 'Apply',
      destroy: 'Destroy',
    };
    const label = typeLabels[operation] || operation;

    return this.page.locator(`[data-testid="task-row"]:has-text("${moduleName}"):has-text("${label}")`).first();
  }

  /**
   * Get task status
   */
  async getTaskStatus(moduleName: string, operation: string): Promise<string> {
    const taskRow = this.findTask(moduleName, operation as any);
    const statusBadge = taskRow.locator('[data-testid="task-status"]').first();
    return await statusBadge.textContent() || '';
  }

  /**
   * Click on task to view details/logs
   */
  async openTask(moduleName: string, operation: string) {
    const taskRow = this.findTask(moduleName, operation as any);
    await taskRow.click();

    // Wait for logs/details to appear
    await this.page.waitForTimeout(2000);
  }

  /**
   * Get task logs
   */
  async getTaskLogs(moduleName: string, operation: string): Promise<string> {
    await this.openTask(moduleName, operation);

    // Find logs container
    const logsContainer = this.page.locator('[data-testid="task-logs"], .logs, pre, code').first();
    return await logsContainer.textContent() || '';
  }

  /**
   * Wait for task to complete (success or failure)
   */
  async waitForTaskCompletion(
    moduleName: string,
    operation: string,
    timeoutMs: number = 120000
  ): Promise<{ status: string; success: boolean }> {
    const startTime = Date.now();
    const pollInterval = 3000;

    while (Date.now() - startTime < timeoutMs) {
      try {
        // First, wait for the task row to exist
        const taskRow = this.findTask(moduleName, operation as any);
        await taskRow.waitFor({ state: 'visible', timeout: 5000 });

        const status = await this.getTaskStatus(moduleName, operation);
        const statusLower = status.toLowerCase();

        // Check for terminal states
        if (statusLower.includes('success') || statusLower.includes('completed')) {
          return { status, success: true };
        }

        if (statusLower.includes('failed') || statusLower.includes('error')) {
          return { status, success: false };
        }

        // Refresh to get latest status
        await this.page.reload({ waitUntil: 'networkidle' });

        // Wait before next check
        await this.page.waitForTimeout(pollInterval);
      } catch (error) {
        // If task row not found yet, wait and retry
        await this.page.waitForTimeout(pollInterval);

        // Reload in case the task hasn't appeared yet
        await this.page.reload({ waitUntil: 'networkidle' });
      }
    }

    throw new Error(`Timeout waiting for task ${moduleName}:${operation} to complete`);
  }

  /**
   * Verify all tasks for a module succeeded
   */
  async verifyModuleTasksSucceeded(moduleName: string, operations: string[]): Promise<boolean> {
    for (const operation of operations) {
      const result = await this.waitForTaskCompletion(moduleName, operation);
      if (!result.success) {
        console.error(`Task ${moduleName}:${operation} failed with status: ${result.status}`);
        return false;
      }
    }
    return true;
  }

  /**
   * Get latest task for module and operation
   */
  async getLatestTask(moduleName: string, operation: string): Promise<{
    status: string;
    startTime?: string;
    endTime?: string;
    duration?: string;
  }> {
    const status = await this.getTaskStatus(moduleName, operation);

    return {
      status,
    };
  }

  /**
   * Filter tasks by status
   * Uses sidebar status filter buttons
   */
  async filterByStatus(status: 'running' | 'completed' | 'failed') {
    // Map to sidebar button labels
    const statusLabels: Record<string, string> = {
      running: 'Running',
      completed: 'Completed',
      failed: 'Failed',
    };
    const label = statusLabels[status] || status;

    const filterButton = this.page.locator(`button:has-text("${label}")`).first();
    if (await filterButton.isVisible({ timeout: 2000 }).catch(() => false)) {
      await filterButton.click();
    }
  }

  /**
   * Check if any tasks are currently running
   */
  async hasRunningTasks(): Promise<boolean> {
    const runningTasks = this.page.locator('[data-testid="task-status"]:has-text("Running")');
    return (await runningTasks.count()) > 0;
  }
}
