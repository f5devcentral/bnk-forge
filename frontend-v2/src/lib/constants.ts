/**
 * Application-wide constants
 *
 * Centralized numeric constants to improve code readability and maintainability.
 * Replace magic numbers with named constants to make intent clear.
 */

/**
 * TanStack Query staleTime values (milliseconds)
 * Controls how long data is considered fresh before refetching
 */
export const QUERY_STALE_TIME = {
  /** No caching - always refetch when invalidated */
  NEVER: 0,

  /** Very fresh data - 2 seconds (system health checks) */
  VERY_SHORT: 2000,

  /** Real-time data - 3 seconds (rollout status during active deployment) */
  REALTIME: 3000,

  /** Fresh data - 5 seconds (system stats, metrics that change frequently) */
  SHORT: 5000,

  /** Medium freshness - 10 seconds (K8s events, resource status) */
  MEDIUM: 10000,

  /** Standard - 15 seconds (system info) */
  DEFAULT_MEDIUM: 15000,

  /** Default cache - 30 seconds (most queries) */
  DEFAULT: 30000,

  /** Long cache - 1 minute (container lists, pod details) */
  LONG: 60000,

  /** Very long cache - 5 minutes (resource types, relatively static data) */
  VERY_LONG: 5 * 60 * 1000,

  /** Extended cache - 10 minutes (Helm charts, infrequently changing data) */
  EXTENDED: 10 * 60 * 1000,
  
  /** PERF-014: System data cache - 2 minutes (health, metrics - backend caches anyway) */
  SYSTEM: 2 * 60 * 1000,
} as const;

/**
 * Debounce delays for user input (milliseconds)
 * Controls how long to wait after user stops typing before triggering action
 */
export const DEBOUNCE_MS = {
  /** Default debounce - 300ms (standard search inputs) */
  DEFAULT: 300,

  /** Search debounce - 300ms (K8s, F5BNK resource search) */
  SEARCH: 300,

  /** API-heavy search - 800ms (Helm chart search with remote API calls) */
  API_SEARCH: 800,
} as const;

/**
 * Display limits for lists and previews
 * Controls how many items to show before truncating
 */
export const DISPLAY_LIMITS = {
  /** Attention items on dashboard - show top 2 issues */
  DASHBOARD_ATTENTION: 2,

  /** Dependency preview - show first 3 */
  DEPENDENCY_PREVIEW: 3,

  /** Description preview - show first 3 lines */
  DESCRIPTION_PREVIEW: 3,

  /** Recent activity - show last 5 items */
  RECENT_ACTIVITY: 5,

  /** Command palette results - show 5 per category */
  COMMAND_PALETTE_CATEGORY: 5,

  /** Module variables preview - show first 5 */
  MODULE_VARIABLES_PREVIEW: 5,

  /** Recent errors panel - show last 5 errors */
  RECENT_ERRORS: 5,

  /** Dashboard cards - show 6 projects/clusters */
  DASHBOARD_CARDS: 6,

  /** Command palette all results - show up to 10 */
  COMMAND_PALETTE_ALL: 10,

  /** Address list preview - show first 10 */
  ADDRESS_LIST_PREVIEW: 10,
} as const;

/**
 * Character limits for text truncation
 */
export const TEXT_LIMITS = {
  /** Module description truncation - 60 characters */
  DESCRIPTION_SHORT: 60,
} as const;

/**
 * Polling/refetch intervals for React Query (milliseconds)
 * Controls how frequently queries automatically refetch in the background
 */
export const POLL_INTERVALS = {
  /** Fast polling - 5 seconds (active task progress, deployment status) */
  FAST: 5000,

  /** Standard polling - 10 seconds (resource lists during active operations) */
  STANDARD: 10000,

  /** Medium polling - 20 seconds (K8s resource lists, cluster health) */
  MEDIUM: 20000,

  /** Slow polling - 30 seconds (general resource lists, operator status) */
  SLOW: 30000,

  /** Very slow polling - 60 seconds (fleet health, system metrics) */
  VERY_SLOW: 60000,

  /** Background polling - 2 minutes (drift summaries, cost summaries) */
  BACKGROUND: 2 * 60 * 1000,

  /** Lazy polling - 3 minutes (cost trends, slowly changing data) */
  LAZY: 3 * 60 * 1000,
} as const;

/**
 * Drift detection schedule intervals (seconds)
 * Controls how frequently drift checks run
 */
export const DRIFT_SCHEDULE_INTERVALS = {
  /** 5 minutes - Very frequent checking */
  FIVE_MIN: '300',

  /** 30 minutes - Frequent checking */
  THIRTY_MIN: '1800',

  /** 2 hours - Standard checking */
  TWO_HOURS: '7200',

  /** 12 hours - Infrequent checking */
  TWELVE_HOURS: '43200',

  /** 24 hours - Daily checking */
  TWENTYFOUR_HOURS: '86400',
} as const;

/**
 * Human-readable labels for drift schedule intervals
 */
export const DRIFT_SCHEDULE_LABELS: Record<string, string> = {
  '300': '5 minutes',
  '1800': '30 minutes',
  '7200': '2 hours',
  '43200': '12 hours',
  '86400': '24 hours',
} as const;
