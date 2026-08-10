/**
 * System administration and monitoring types
 */

export interface ProcessMetrics {
  cpu_percent: number;
  cpu_count: number;
  rss_bytes: number;
  vms_bytes: number;
  num_threads: number;
  open_fds: number | null;
  net_rx_bytes: number;
  net_tx_bytes: number;
  uptime_seconds: number;
  sampled_at: number;
}

export interface ServiceStatus {
  status: 'healthy' | 'degraded' | 'offline';
  response_time_ms?: number | null;
  workers?: number;
  active_tasks?: number;
  error?: string;
}

export interface SystemHealth {
  services: {
    backend: ServiceStatus;
    database: ServiceStatus;
    redis: ServiceStatus;
    celery: ServiceStatus;
  };
  timestamp: string;
}

export interface QueueInfo {
  pending: number;
  active: number;
}

export interface QueueMetrics {
  queues: {
    default: QueueInfo;
    opentofu: QueueInfo;
  };
  workers: {
    total: number;
    active: number;
    offline: number;
    // Optional - indicates inspection timeout occurred
    inspection_timeout?: boolean;
  };
  tasks: {
    pending: number;
    active: number;
    completed_last_hour: number;
  };
}

export interface PerformanceMetrics {
  api: {
    avg_response_time_ms: number | null;
    requests_last_hour: number;
    failed_requests_last_hour: number;
    // Task-specific metrics (optional - may be returned by newer backends)
    avg_task_duration_seconds?: number | null;
    tasks_last_hour?: number;
    failed_tasks_last_hour?: number;
  };
  database: {
    size_mb: number | null;
    connections: number;
    slow_queries_last_hour: number;
  };
  tasks: {
    avg_duration_seconds: number | null;
    longest_running: {
      id: number;
      duration: number | null;
      type: string;
    } | null;
  };
}

export interface TaskError {
  task_id: number;
  type: string;
  error: string;
  timestamp: string;
  project: string;
  module: string | null;
  exit_code: number | null;
}

export interface ErrorsList {
  errors: TaskError[];
  total: number;
}

export interface TableStats {
  rows: number;
  size_mb: number;
}

export interface DatabaseStats {
  size_mb: number;
  tables: {
    tasks: TableStats;
    deployment_logs: TableStats;
    audit_logs: TableStats;
  };
}

export interface CleanupResult {
  deleted: number;
  freed_mb: number;
}

export interface VacuumResult {
  status: 'success' | 'skipped';
  duration_seconds: number;
  message?: string;
}

export type CleanupType = 'deployment_logs' | 'audit_logs' | 'completed_tasks';


// ============================================================================
// System Upgrade types (UP-013)
// ============================================================================

export interface UpgradeReadiness {
  host_repo_path_set: boolean;
  docker_socket_available: boolean;
  upgrade_ready: boolean;
  upgrade_in_progress: boolean;
  deployment_mode?: 'local' | 'server' | string;
  recommended_command?: string | null;
  recommended_label?: string | null;
  gui_upgrade_supported?: boolean;
}

export interface VersionInfo {
  current_version: string;
  latest_version: string;
  update_available: boolean;
  commits_behind: number;
  error?: string;
  upgrade_readiness?: UpgradeReadiness;
}

export interface UpgradeResponse {
  status: string;
  message: string;
  old_version?: string;
  new_version?: string;
  note?: string;
  active_tasks?: number;
  readiness?: UpgradeReadiness;
}

export interface UpgradeState {
  status: string;
  old_version?: string | null;
  new_version?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  current_phase?: string | null;
  phase_label?: string | null;
  pre_upgrade_commit?: string | null;
  log: string[];
}

export interface UpgradeVerification {
  verdict: 'healthy' | 'degraded' | 'unhealthy';
  checks: Record<string, { status: string; error?: string; note?: string; [key: string]: unknown }>;
  timestamp: string;
}
