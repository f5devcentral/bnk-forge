/**
 * Proxy Migration types (D-021 P3).
 * Guided, gated, reversible proxy-to-BNK cutover.
 */

export type ProxyMigrationStatus =
  | 'pending'
  | 'in_progress'
  | 'awaiting_verify'
  | 'verify_failed'
  | 'awaiting_cutover'
  | 'cutover_confirmed'
  | 'awaiting_teardown'
  | 'completed'
  | 'failed'
  | 'aborted'
  | 'rolled_back';

export type MigrationStepStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped'
  | 'blocked'
  | 'awaiting_confirm';

export interface ProxyMigrationStep {
  id: number;
  step_index: number;
  name: string;
  description: string;
  status: MigrationStepStatus;
  requires_confirm: boolean;
  output_log: string | null;
  result_data: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
}

export interface VerifyResult {
  programmed: boolean;
  reachable: boolean;
  passed: boolean;
  reasons: string[];
}

export interface ProxyMigration {
  id: number;
  cluster_id: number;
  project_id: number | null;
  source_descriptor: Record<string, unknown>;
  bnk_gateway_name: string | null;
  bnk_gateway_namespace: string | null;
  verify_result: VerifyResult | null;
  teardown_info: Record<string, unknown> | null;
  status: ProxyMigrationStatus;
  current_step: string | null;
  error_message: string | null;
  error_step: string | null;
  triggered_by: string | null;
  cutover_confirmed_by: string | null;
  cutover_confirmed_at: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  steps: ProxyMigrationStep[];
}

export interface ProxyMigrationListResponse {
  migrations: ProxyMigration[];
  count: number;
  cluster_id: number;
}

export interface CreateMigrationRequest {
  source_descriptor: Record<string, unknown>;
  combined_yaml: string;
  project_id?: number;
  teardown_info?: Record<string, unknown>;
}

export interface ConfirmCutoverRequest {
  confirmed_by: string;
}

export interface ConfirmTeardownRequest {
  confirmed_by: string;
}

export interface AbortMigrationRequest {
  by?: string;
}

export interface MigrationActionResponse {
  success: boolean;
  message: string;
  migration_id: number;
  status: string;
}

/** Step names in order. */
export const MIGRATION_STEP_NAMES = [
  'translate',
  'deploy_bnk',
  'verify',
  'cutover',
  'teardown_old',
] as const;

export type MigrationStepName = (typeof MIGRATION_STEP_NAMES)[number];

/** Human-readable step labels. */
export const MIGRATION_STEP_LABELS: Record<MigrationStepName, string> = {
  translate: 'Translate',
  deploy_bnk: 'Deploy BNK',
  verify: 'Verify',
  cutover: 'Cut Over',
  teardown_old: 'Teardown Old Proxy',
};

/** Terminal statuses — no further transitions possible. */
export const TERMINAL_MIGRATION_STATUSES: ProxyMigrationStatus[] = [
  'completed',
  'failed',
  'aborted',
  'rolled_back',
  'verify_failed',
];

/** Statuses that should trigger polling. */
export const POLLING_MIGRATION_STATUSES: ProxyMigrationStatus[] = [
  'pending',
  'in_progress',
  'cutover_confirmed',
];
