/**
 * E2E-CRITICAL-002: Realistic Fixture Matrix
 *
 * Defines test scenario states for healthy and degraded platform conditions.
 * Used by critical workflow tests to validate behavior across different
 * platform states.
 *
 * These are API response shapes that can be seeded or asserted against
 * when testing against a real backend.
 */

// ================================================================
// Scenario 1: Healthy Platform
// ================================================================

/** Expected health endpoint response when everything is up */
export const HEALTHY_PLATFORM = {
  health: {
    status: 'healthy',
    checks: {
      database: 'ok',
      redis: 'ok',
      celery: 'ok',
    },
  },
  expectations: {
    dashboardLoads: true,
    sidebarComplete: true,
    apiResponds: true,
    healthBadgeColor: /green|emerald/,
  },
};

// ================================================================
// Scenario 2: Healthy Cluster
// ================================================================

/** Expected state when a K8s cluster is connected and responsive */
export const HEALTHY_CLUSTER = {
  expectations: {
    clusterListNonEmpty: true,
    statusBadge: /connected|online|healthy/i,
    resourceCountAboveZero: true,
    namespaceListLoads: true,
  },
  /** Minimum fields a cluster card should display */
  requiredFields: ['name', 'status', 'version', 'nodes'],
};

// ================================================================
// Scenario 3: Empty State (New Installation)
// ================================================================

/** Expected state when no projects, clusters, or modules exist */
export const EMPTY_PLATFORM = {
  expectations: {
    projectListEmpty: true,
    emptyStateVisible: true,
    createButtonVisible: true,
    noErrorsShown: true,
  },
  /** Pages that should show empty state gracefully */
  emptyPages: [
    { path: '/projects', emptyText: /no projects|get started|create/i },
    { path: '/kubernetes', emptyText: /no clusters|connect|import/i },
    { path: '/stacks', emptyText: /no stacks|browse|create/i },
    { path: '/fleet', emptyText: /no clusters|add/i },
  ],
};

// ================================================================
// Scenario 4: Auth Failure States
// ================================================================

export const AUTH_SCENARIOS = {
  /** Invalid credentials */
  invalidLogin: {
    username: 'nonexistent',
    password: 'wrongpassword',
    expectedError: /invalid|incorrect|failed/i,
    expectRedirect: false,
  },
  /** Empty credentials */
  emptyCredentials: {
    username: '',
    password: '',
    expectedBehavior: 'submit-disabled-or-validation-error',
  },
  /** Expired session (token removed) */
  expiredSession: {
    setup: 'clear-localStorage-auth-token',
    expectedRedirect: '/login',
    expectToast: false,  // silent redirect, not error toast
  },
};

// ================================================================
// Scenario 5: Network / API Failure States
// ================================================================

export const API_FAILURE_SCENARIOS = {
  /** Backend returns 500 */
  serverError: {
    expectedBehavior: 'error-state-with-retry',
    retryButtonVisible: true,
    toastSeverity: 'error',
  },
  /** Backend unreachable (network error) */
  networkError: {
    expectedBehavior: 'connection-error-message',
    retryButtonVisible: true,
  },
  /** Slow response (near timeout) */
  slowResponse: {
    thresholdMs: 10_000,
    expectedBehavior: 'loading-spinner-visible',
  },
};

// ================================================================
// Scenario 6: Fleet Health States
// ================================================================

export const FLEET_SCENARIOS = {
  /** All clusters healthy */
  allHealthy: {
    expectations: {
      healthBadge: /healthy|online/i,
      warningCount: 0,
      criticalCount: 0,
    },
  },
  /** Mixed health — some clusters degraded */
  degraded: {
    expectations: {
      someUnhealthy: true,
      attentionCardsVisible: true,
      healthBadgeMixed: /degraded|warning|partial/i,
    },
  },
  /** No clusters connected */
  noClusters: {
    expectations: {
      emptyStateVisible: true,
      addClusterPromptVisible: true,
    },
  },
};

// ================================================================
// Scenario 7: BNK Gateway States
// ================================================================

export const BNK_SCENARIOS = {
  /** Cluster with BNK components installed */
  bnkInstalled: {
    expectations: {
      healthDashboardLoads: true,
      gatewayClassesNonEmpty: true,
      topologyRenders: true,
    },
  },
  /** Cluster without BNK components */
  bnkNotInstalled: {
    expectations: {
      emptyStateOrInstallPrompt: true,
      noErrors: true,
    },
  },
};

// ================================================================
// Scenario 8: RBAC Permission States
// ================================================================

export const RBAC_SCENARIOS = {
  admin: {
    visiblePages: ['/', '/projects', '/kubernetes', '/fleet', '/f5-bnk', '/stacks', '/system', '/helm'],
    canCreate: true,
    canDelete: true,
    canManageUsers: true,
  },
  operator: {
    visiblePages: ['/', '/projects', '/kubernetes', '/fleet', '/f5-bnk', '/stacks', '/helm'],
    canCreate: true,
    canDelete: false,
    canManageUsers: false,
  },
  viewer: {
    visiblePages: ['/', '/projects', '/kubernetes', '/fleet', '/f5-bnk'],
    canCreate: false,
    canDelete: false,
    canManageUsers: false,
  },
};

// ================================================================
// Scenario 9: Module Lifecycle States
// ================================================================

export const MODULE_STATUS_EXPECTATIONS = {
  /** Fresh module just added */
  uninitialized: {
    status: /not_initialized|pending/i,
    allowedActions: ['Initialize', 'Remove'],
  },
  /** After successful init */
  initialized: {
    status: /initialized|ready/i,
    allowedActions: ['Plan', 'Remove'],
  },
  /** After successful plan */
  planned: {
    status: /planned/i,
    allowedActions: ['Apply', 'Plan', 'Remove'],
  },
  /** After successful apply */
  applied: {
    status: /applied|deployed/i,
    allowedActions: ['Plan', 'Destroy'],
  },
  /** Error state */
  errored: {
    status: /error|failed/i,
    retryVisible: true,
  },
};

// ================================================================
// Timeout Presets for Different Scenarios
// ================================================================

export const SCENARIO_TIMEOUTS = {
  /** Fast UI interactions */
  uiAction: 5_000,
  /** API call + render */
  apiRoundtrip: 15_000,
  /** Page with multiple API calls */
  heavyPage: 30_000,
  /** K8s resource fetch (may be slow for large clusters) */
  k8sQuery: 20_000,
  /** BNK resource discovery */
  bnkDiscovery: 20_000,
};
