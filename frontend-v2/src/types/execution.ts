// Parallel Execution types

import type { JsonValue } from './common';

export interface ExecutionLayerModule {
  id: number;
  name: string;
  path: string;
  estimated_time_minutes: number;
  status: string;
}

export interface ExecutionLayer {
  layer_index: number;
  // Used in execution plan response
  modules?: ExecutionLayerModule[];
  // Used in parallel execution status response
  module_ids?: number[];
  module_names?: string[];
  module_count: number;
  estimated_time_minutes?: number;
  can_run_parallel?: boolean;
  status?: 'pending' | 'in_progress' | 'completed' | 'failed';
  results?: Record<number, { success: boolean; task_id?: number; error?: string | null; outputs?: Record<string, JsonValue> }>;
}

export interface ExecutionPlan {
  layers: ExecutionLayer[];
  total_layers: number;
  total_modules: number;
  total_estimated_time_sequential: number;
  total_estimated_time_parallel: number;
  time_savings_minutes: number;
  time_savings_percent: number;
  parallelization_factor: number;
  error?: string;
}

// ============================================================================
// New run-progress types (D-001 Phase 3 S2) — derived from Task rows via
// GET /api/projects/{project_id}/orchestration/{run_handle}
// ============================================================================

/** Per-module result within a layer (mirrors backend LayerResult) */
export interface LayerResult {
  module_id: number;
  success: boolean;
  task_id?: number | null;
  error?: string | null;
}

/** Per-layer progress entry (mirrors backend LayerProgress) */
export interface LayerProgress {
  layer_index: number;
  module_ids: number[];
  module_names: string[];
  /** "pending" | "in_progress" | "completed" | "failed" */
  status: string;
  /** String-keyed map (module_id as string → LayerResult) from Python JSON serialization */
  results: Record<string, LayerResult>;
}

/**
 * Run progress shape returned by
 * GET /api/projects/{project_id}/orchestration/{run_handle}
 * (mirrors backend RunProgress Pydantic model, D-001 Phase 3 S2)
 */
export interface RunProgress {
  run_handle: string;
  project_id: number;
  action: 'deploy' | 'destroy' | null;
  /** "in_progress" | "completed" | "failed" */
  status: string;
  total_layers: number;
  current_layer: number;
  layers: LayerProgress[];
  successful_modules: number[];
  failed_modules: number[];
  skipped_modules: number[];
  started_at?: string | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
  progress_percent: number;
  error_message?: string | null;
}

// ============================================================================
// Legacy types — kept for backward compat (old PE routes still exist during S3b)
// ============================================================================

export interface ParallelExecutionStatus {
  id: number;
  project_id: number;
  action: 'deploy' | 'destroy';
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'cancelled';
  current_layer: number;
  total_layers: number;
  layers_data: ExecutionLayer[];
  execution_mode: 'parallel' | 'sequential';
  successful_modules: number[];
  failed_modules: number[];
  skipped_modules: number[];
  error_message?: string;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  estimated_duration_minutes?: number;
  progress_percent: number;
  triggered_by: string;
  created_at: string;
}

export interface ParallelExecutionSummary {
  id: number;
  action: 'deploy' | 'destroy';
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'cancelled';
  current_layer: number;
  total_layers: number;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  successful_modules: number;
  failed_modules: number;
  triggered_by: string;
}

export interface DeployAllResponse {
  orchestrator_task_id: string;
  execution_plan: ExecutionPlan;
  total_modules: number;
  total_layers: number;
  estimated_time_minutes: number;
}
