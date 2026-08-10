/**
 * Centralized status color definitions — token-pure (D-020).
 *
 * All classes reference semantic design tokens (success / warning / destructive /
 * info / muted) instead of raw palette classes.  Callers keep their existing
 * function signatures; only the emitted class strings change.
 */

/**
 * Module status colors (OpenTofu/Terraform modules)
 * Used in ProjectDetailV2 and module-related components
 */
export const MODULE_STATUS_COLORS = {
  applied:         'bg-success/10 text-success border-success/20',
  initializing:    'bg-warning/10 text-warning border-warning/20',
  initialized:     'bg-info/10 text-info border-info/20',
  planned:         'bg-info/10 text-info border-info/20',
  planning:        'bg-warning/10 text-warning border-warning/20',
  applying:        'bg-warning/10 text-warning border-warning/20',
  destroying:      'bg-warning/10 text-warning border-warning/20',
  not_initialized: 'bg-muted text-muted-foreground border-border',
  // Failed states
  failed:          'bg-destructive/10 text-destructive border-destructive/20',
  error:           'bg-destructive/10 text-destructive border-destructive/20',
  init_failed:     'bg-destructive/10 text-destructive border-destructive/20',
  plan_failed:     'bg-destructive/10 text-destructive border-destructive/20',
  apply_failed:    'bg-destructive/10 text-destructive border-destructive/20',
  destroy_failed:  'bg-destructive/10 text-destructive border-destructive/20',
} as const;

/**
 * Task/Job status colors
 * Used in TaskHistory and deployment tracking
 */
export const TASK_STATUS_COLORS = {
  completed:   'bg-success/10 text-success border-success/20',
  in_progress: 'bg-info/10 text-info border-info/20',
  failed:      'bg-destructive/10 text-destructive border-destructive/20',
  cancelled:   'bg-muted text-muted-foreground border-border',
  queued:      'bg-warning/10 text-warning border-warning/20',
  pending:     'bg-warning/10 text-warning border-warning/20',
} as const;

/**
 * Kubernetes/Helm resource status colors (pattern-based)
 * Used for K8s pods, deployments, services, and Helm releases
 *
 * @param status - Status string from K8s/Helm resource
 * @returns Tailwind CSS classes for badge styling
 */
export function getResourceStatusColor(status: string): string {
  if (!status) return 'bg-muted text-muted-foreground border-border';

  const statusLower = status.toLowerCase();

  // Error/Failed states (CHECK FIRST - before "ready" substring match)
  if (
    statusLower.includes('failed') ||
    statusLower.includes('error') ||
    statusLower.includes('crashloopbackoff') ||
    statusLower.includes('imagepullbackoff') ||
    statusLower.includes('uninstalled') ||
    statusLower.includes('notready')  // Must check before "ready"
  ) {
    return 'bg-destructive/10 text-destructive border-destructive/20';
  }

  // Success states (after error checks)
  if (
    statusLower.includes('running') ||
    statusLower.includes('ready') ||
    statusLower.includes('deployed') ||
    statusLower.includes('active') ||
    statusLower.includes('bound')
  ) {
    return 'bg-success/10 text-success border-success/20';
  }

  // Warning/Pending states
  if (
    statusLower.includes('pending') ||
    statusLower.includes('waiting') ||
    statusLower.includes('superseded') ||
    statusLower.includes('unknown') ||
    statusLower.includes('partial')
  ) {
    return 'bg-warning/10 text-warning border-warning/20';
  }

  // Terminating/Removing states
  if (
    statusLower.includes('terminating') ||
    statusLower.includes('deleting') ||
    statusLower.includes('uninstalling')
  ) {
    return 'bg-muted text-muted-foreground border-border';
  }

  // Default neutral state
  return 'bg-muted text-muted-foreground border-border';
}

/**
 * Get module status color by status string
 * Helper function for backwards compatibility
 */
export function getModuleStatusColor(status: string): string {
  return MODULE_STATUS_COLORS[status as keyof typeof MODULE_STATUS_COLORS] ||
         MODULE_STATUS_COLORS.not_initialized;
}

/**
 * Get task status color by status string
 * Helper function for backwards compatibility
 */
export function getTaskStatusColor(status: string): string {
  return TASK_STATUS_COLORS[status as keyof typeof TASK_STATUS_COLORS] ||
         TASK_STATUS_COLORS.pending;
}
