/**
 * E2E Test Configuration
 *
 * Central configuration for all E2E tests.
 * Update these values based on your environment.
 */

export const TEST_CONFIG = {
  // Application URLs
  baseUrl: process.env.TEST_BASE_URL || 'https://localhost',
  apiUrl: process.env.TEST_API_URL || 'http://localhost:8000',

  // Default credentials
  credentials: {
    admin: { username: 'admin', password: 'e2e-Admin-Pass-1' },
    // operator and viewer roles — created by system admin tests or pre-seeded
    operator: { username: 'e2e-operator', password: 'changeme' },
    viewer: { username: 'e2e-viewer', password: 'changeme' },
  },

  // Test behavior flags
  flags: {
    // Set to true to leave test resources for manual inspection
    // Set to false to automatically clean up after tests
    skipCleanup: process.env.SKIP_CLEANUP === 'true' || false,

    // Set to true to run AWS verification checks
    // Requires AWS credentials to be configured
    verifyAwsResources: process.env.VERIFY_AWS === 'true' || false,

    // Set to true to take screenshots at each major step
    captureScreenshots: process.env.SCREENSHOTS === 'true' || true,
  },

  // Test data
  testProject: {
    name: `e2e-test-${Date.now()}`,
    description: 'Automated E2E test project - safe to delete',
    awsRegion: process.env.AWS_REGION || 'ap-southeast-2',
  },

  // Module configuration for dependency chain test
  // Note: Paths must match the actual module paths in the database
  // Format: "infra/{provider}/{module}" or just the module name
  modules: {
    vpc: {
      name: 'vpc',
      path: 'infra/aws/vpc', // Updated to match actual module library format
      variables: {
        vpc_cidr: '10.0.0.0/16',
      },
    },
    security: {
      name: 'security',
      path: 'infra/aws/security', // Updated to match actual module library format
      variables: {
        user_ip: '0.0.0.0/0', // Allow from anywhere for test
      },
    },
    eks: {
      name: 'eks',
      path: 'infra/aws/eks', // Updated to match actual module library format
      variables: {
        cluster_name: 'e2e-test-cluster',
        kubernetes_version: '1.28',
      },
    },
  },

  // Timeouts (in milliseconds)
  timeouts: {
    navigation: 30000,        // 30s for page loads
    shortWait: 5000,          // 5s for quick operations
    mediumWait: 30000,        // 30s for API calls
    longWait: 60000,          // 1m for module operations
    terraformInit: 120000,    // 2m for tofu init
    terraformPlan: 180000,    // 3m for tofu plan
    terraformApply: 600000,   // 10m for tofu apply (VPC, Security, EKS)
    terraformDestroy: 600000, // 10m for tofu destroy
    taskPolling: 5000,        // 5s between task status checks
  },

  // AWS verification settings
  aws: {
    region: process.env.AWS_REGION || 'ap-southeast-2',
    // Credentials will be loaded from environment variables:
    // AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
  },

  // Selectors (for easier maintenance if UI changes)
  // NOTE: These are reference values - actual page objects use specific selectors
  selectors: {
    // Projects page
    createProjectButton: 'button:has-text("New Project")',
    projectCard: '[data-testid="project-card"]', // Table rows with data-testid

    // Module library (in add-module dialog)
    moduleCard: '[role="dialog"] button', // Module buttons in dialog
    addModuleButton: 'button:has-text("Add Module")',

    // Project details - Module rows (table layout)
    moduleRow: '[data-testid="module-card"]', // Table rows with data-testid
    moduleStatusBadge: '[data-testid="module-status"]',
    moduleActionsMenu: '[data-testid="module-actions-menu"]', // 3-dot menu trigger
    // Actions are inside dropdown menu (click module-actions-menu first):
    initMenuItem: '[role="menuitem"]:has-text("Initialize")',
    planMenuItem: '[role="menuitem"]:has-text("Plan")',
    applyMenuItem: '[role="menuitem"]:has-text("Apply")',
    destroyMenuItem: '[role="menuitem"]:has-text("Destroy")',

    // Tasks page
    taskRow: '[data-testid="task-row"]',
    taskStatus: '[data-testid="task-status"]',
    taskLogs: '[data-testid="task-logs"]',
  },
};

/**
 * Helper to get timeout for specific operation
 */
export function getTimeout(operation: keyof typeof TEST_CONFIG.timeouts): number {
  return TEST_CONFIG.timeouts[operation];
}

/**
 * Helper to check if cleanup should be skipped
 */
export function shouldSkipCleanup(): boolean {
  return TEST_CONFIG.flags.skipCleanup;
}

/**
 * Helper to check if AWS verification should run
 */
export function shouldVerifyAws(): boolean {
  return TEST_CONFIG.flags.verifyAwsResources;
}
