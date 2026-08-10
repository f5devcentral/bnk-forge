/**
 * Types for Config Snapshots and Rollback (UX-011).
 *
 * Mirrors the backend Pydantic models and API responses
 * for snapshot CRUD, restore, and diff operations.
 */

// ============================================================================
// Core Types
// ============================================================================

/** Trigger type for how the snapshot was created */
export type SnapshotTrigger =
  | 'manual'
  | 'auto-before-apply'
  | 'auto-before-destroy'
  | 'auto-before-upgrade';

/** Summary representation of a snapshot (list view — no config_data) */
export interface ConfigSnapshotSummary {
  id: number;
  project_id: number;
  cluster_id: number | null;
  trigger: SnapshotTrigger;
  label: string | null;
  created_by: string | null;
  resource_count: number;
  module_count: number;
  created_at: string; // ISO datetime
}

/** Full snapshot detail including config data */
export interface ConfigSnapshotDetail extends ConfigSnapshotSummary {
  config_data: SnapshotConfigData;
}

/** The exported config structure stored in each snapshot */
export interface SnapshotConfigData {
  bnk_forge_export: {
    version: string;
    exported_at: string;
    cluster: {
      name: string;
      id: number | null;
      k8s_version?: string;
      api_server?: string;
      cloud_provider?: string;
    };
    project: {
      name: string;
      id: number | null;
      project_type: string | null;
    };
    export_metadata: {
      resource_counts: Record<string, number>;
      total_resources: number;
      categories: string[];
      duration_ms?: number;
      snapshot_type?: string; // 'module_only' if no cluster
    };
  };
  resources: Record<string, Array<Record<string, unknown>>>;
  module_config: Record<string, {
    variables: Record<string, unknown>;
    status: string;
    outputs: Record<string, unknown>;
  }>;
}


// ============================================================================
// API Request/Response Types
// ============================================================================

/** Request to create a manual snapshot */
export interface CreateSnapshotRequest {
  label?: string;
  cluster_id?: number;
}

/** Response from listing snapshots */
export interface SnapshotListResponse {
  snapshots: ConfigSnapshotSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** Response from creating a snapshot */
export interface CreateSnapshotResponse extends ConfigSnapshotSummary {
  message: string;
}

/** Request to diff two snapshots */
export interface DiffSnapshotsRequest {
  snapshot_a_id: number;
  snapshot_b_id: number;
}

/** Response from restoring a snapshot */
export interface RestoreSnapshotResponse {
  message: string;
  results: {
    applied: Array<{ kind: string; name: string; namespace: string }>;
    failed: Array<{ kind: string; name: string; namespace: string; error: string }>;
    skipped: Array<{ kind: string; name: string; namespace: string; reason: string }>;
  };
  snapshot_id: number;
  target_cluster: string;
}

/** Changed resource in a diff */
export interface DiffChangedResource {
  resource: string;
  kind: string;
  spec_a: Record<string, unknown>;
  spec_b: Record<string, unknown>;
}

/** Changed module in a diff */
export interface DiffChangedModule {
  module_path: string;
  variables_a: Record<string, unknown>;
  variables_b: Record<string, unknown>;
}

/** Snapshot metadata in diff response */
export interface DiffSnapshotMeta {
  id: number;
  trigger: SnapshotTrigger;
  label: string | null;
  created_at: string | null;
}

/** Response from diffing two snapshots */
export interface SnapshotDiffResponse {
  summary: string;
  total_diffs: number;
  cluster_a: string;
  cluster_b: string;
  resources: {
    only_in_a: string[];
    only_in_b: string[];
    changed: DiffChangedResource[];
  };
  modules: {
    only_in_a: string[];
    only_in_b: string[];
    changed: DiffChangedModule[];
  };
  snapshot_a: DiffSnapshotMeta;
  snapshot_b: DiffSnapshotMeta;
}
