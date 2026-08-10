// Drift Detection types

import type { JsonValue } from './common';
import type { DeploymentEngineType } from './modules';

export interface DriftSettings {
  id: number;
  project_id: number;
  enabled: boolean;
  schedule_type: "cron" | "interval";
  schedule_value: string;
  check_all_modules: boolean;
  module_ids?: number[];
  notify_on_drift: boolean;
  notification_channels?: string[];
  notification_config?: Record<string, JsonValue>;
  ignore_insignificant_changes: boolean;
  ignore_patterns?: string[];
  created_at: string;
  updated_at: string;
}

export interface DriftSettingsRequest {
  enabled?: boolean;
  schedule_type?: "cron" | "interval";
  schedule_value?: string;
  check_all_modules?: boolean;
  module_ids?: number[];
  notify_on_drift?: boolean;
  notification_channels?: string[];
  notification_config?: Record<string, JsonValue>;
  ignore_insignificant_changes?: boolean;
  ignore_patterns?: string[];
}

export interface DriftCheck {
  id: number;
  project_id: number;
  module_id?: number;
  module_name?: string;
  schedule_enabled: boolean;
  schedule_cron?: string;
  last_check_at?: string;
  next_check_at?: string;
  drift_detected: boolean;
  drift_summary?: string;
  drift_details?: {
    drift_detected: boolean;
    resource_changes: {
      add: number;
      change: number;
      destroy: number;
    };
    changed_resources: Array<{
      address: string;
      action: string;
    }>;
    summary: string;
  };
  task_id?: number;
  status: "scheduled" | "checking" | "completed" | "failed";
  error_message?: string;
  created_at: string;
  updated_at: string;
}

export interface DriftSummary {
  total_checks: number;
  drift_detected_count: number;
  no_drift_count: number;
  failed_count: number;
  last_check_at?: string;
  projects_with_drift: number;
  modules_with_drift: number;
}

export interface TriggerDriftCheckRequest {
  module_ids?: number[];
}

export interface RecentDriftedItem {
  id: number;
  project_id: number;
  project_name: string;
  module_id: number;
  module_name: string;
  drift_summary: string | null;
  drift_details: DriftCheck['drift_details'] | null;
  last_check_at: string | null;
  resource_changes: {
    add: number;
    change: number;
    destroy: number;
  } | null;
  changed_resources: Array<{
    address: string;
    action: string;
  }>;
}

export interface ClusterDriftModuleStatus {
  module_id: number;
  module_name: string;
  module_path: string;
  engine_type: DeploymentEngineType;
  status: 'ok' | 'drift' | 'unchecked';
  drift_detected: boolean;
  drift_summary: string | null;
  drift_details: Record<string, unknown> | null;
  last_check_at: string | null;
  check_id: number | null;
}

export interface ClusterDriftStatus {
  cluster_id: number;
  project_id: number;
  drift_enabled: boolean;
  total_modules: number;
  modules_with_drift: number;
  modules_ok: number;
  modules_unchecked: number;
  overall_status: 'ok' | 'drift_detected' | 'unchecked' | 'partial';
  module_statuses: ClusterDriftModuleStatus[];
}
