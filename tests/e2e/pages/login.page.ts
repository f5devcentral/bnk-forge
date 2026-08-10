/**
 * Login Page Object (E2E-001)
 *
 * Encapsulates interactions with the Login page.
 * Supports both UI login and direct localStorage injection for fast auth.
 */

import { Page, expect } from '@playwright/test';
import { TEST_CONFIG, getTimeout } from '../config/test-config';

export class LoginPage {
  constructor(private page: Page) {}

  /**
   * Navigate to Login page
   */
  async goto() {
    await this.page.goto('/login');
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Fill username field
   */
  async fillUsername(username: string) {
    await this.page.fill('input[name="username"], input[id="username"]', username);
  }

  /**
   * Fill password field
   */
  async fillPassword(password: string) {
    await this.page.fill('input[name="password"], input[id="password"]', password);
  }

  /**
   * Click the login button
   */
  async clickLogin() {
    await this.page.click('button[type="submit"]');
  }

  /**
   * Perform UI login (fill form + submit)
   */
  async login(username: string, password: string) {
    await this.goto();
    await this.fillUsername(username);
    await this.fillPassword(password);
    await this.clickLogin();
  }

  /**
   * Login and wait for redirect to dashboard
   */
  async loginAndWaitForDashboard(username: string, password: string) {
    await this.login(username, password);
    await this.page.waitForURL('/', { timeout: getTimeout('navigation') });
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Login via API and inject token into localStorage.
   * Much faster than UI login — use for tests that don't test the login flow itself.
   */
  async loginViaApi(username: string = 'admin', password: string = 'changeme') {
    // Call the login API directly
    const response = await this.page.request.post(`${TEST_CONFIG.apiUrl}/api/auth/login`, {
      data: { username, password },
    });

    expect(response.ok()).toBeTruthy();
    const body = await response.json();

    // Inject auth state into localStorage
    await this.page.goto('/login'); // Need a page context to set localStorage
    await this.page.evaluate(
      ({ token, user }) => {
        localStorage.setItem('auth_token', token);
        localStorage.setItem(
          'auth-storage',
          JSON.stringify({
            state: { user, token, isAuthenticated: true },
            version: 0,
          })
        );
      },
      { token: body.token, user: body.user }
    );

    // Navigate to home — AuthGuard should let us through
    await this.page.goto('/');
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Check if the login page is displayed
   */
  async isLoginPage(): Promise<boolean> {
    const loginButton = this.page.locator('button[type="submit"]');
    const usernameField = this.page.locator('input[name="username"], input[id="username"]');
    return (
      (await loginButton.isVisible({ timeout: 2000 }).catch(() => false)) &&
      (await usernameField.isVisible({ timeout: 2000 }).catch(() => false))
    );
  }

  /**
   * Get login error message (if displayed)
   */
  async getErrorMessage(): Promise<string | null> {
    const alert = this.page.locator('[role="alert"]');
    if (await alert.isVisible({ timeout: 2000 }).catch(() => false)) {
      return await alert.textContent();
    }
    return null;
  }

  /**
   * Check if "change password" form is shown after login
   */
  async isChangePasswordFormVisible(): Promise<boolean> {
    const newPasswordField = this.page.locator(
      'input[name="new_password"], input[id="new_password"], input[placeholder*="new password" i]'
    );
    return await newPasswordField.isVisible({ timeout: 2000 }).catch(() => false);
  }
}
