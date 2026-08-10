// Module Library & Project Module types

import type { JsonValue } from './common';

export type DeploymentEngineType = 'kubernetes' | 'opentofu' | 'ansible' | 'script' | 'ssh' | 'container';

export interface LifecycleCapabilities {
  supports_init: boolean;
  supports_plan: boolean;
  supports_apply: boolean;
  supports_destroy: boolean;
  supports_refresh: boolean;
  supports_drift: boolean;
}

export interface ModuleDependency {
  name?: string; // Kept for backward compatibility
  module: string; // Module path (e.g., "infra/aws/vpc")
  type?: string;
  reason?: string;
}

export interface ModuleInput {
  name: string;
  type: string;
  description?: string;
  default?: JsonValue;
  required: boolean;
  source?: "user" | "module" | "computed";
  from_module?: string;
  from_output?: string;
}

export interface ModuleOutput {
  name: string;
  type: string;
  description?: string;
  used_by?: string[];
  value?: JsonValue;
}

export interface ModuleDependenciesMetadata {
  required?: ModuleDependency[];
  optional?: ModuleDependency[];
}

export interface ModuleInputsMetadata {
  required?: ModuleInput[];
  optional?: ModuleInput[];
}

export interface ModulePlatformCompatibility {
  supported_profiles: string[];
  required_capabilities: string[];
  compatibility_scope: 'declared_subset' | 'declared_all_profiles' | 'unspecified';
  declared_any: boolean;
  unmapped_supported_platforms: string[];
}

export interface ModuleLibrary {
  id: number;
  name: string;
  description?: string;
  category: string;
  provider: string;
  version: string;
  variables: ModuleVariable[];
  source_url?: string;
  documentation_url?: string;
  readme?: string;
  dependencies?: string[];
  // PLATFORM-CONTEXT-005 additive compatibility metadata
  supported_platforms?: string[];
  required_capabilities?: string[];
  platform_compatibility?: ModulePlatformCompatibility;
  // New metadata fields from module.json
  dependencies_metadata?: ModuleDependenciesMetadata;
  inputs_metadata?: ModuleInputsMetadata;
  outputs_metadata?: ModuleOutput[];
  deployment_order?: number;
  // Version tracking (P2-1 Phase 2)
  latest_version?: string;
  update_available?: boolean;
  source_version?: string;
  // Public registry / git import provenance + smoke-test (v2_105)
  tarball_sha256?: string;
  upstream_source_url?: string;
  upstream_subpath?: string;
  last_smoke_test_status?: 'pending' | 'passed' | 'warned' | 'failed';
  last_smoke_test_output?: string;
  last_smoke_test_at?: string;
  // Multi-source support (P2-1 / UX-002)
  module_source_id?: number;
  source_path?: string;
  path?: string; // Canonical path identifier (same value as source_path for git-sourced modules)
  // D-033 multi-version catalog: multiple rows may share a path, one per
  // immutable version; is_latest marks the newest version of its (source, path).
  is_latest?: boolean;
  // Free-form tags surfaced in the catalog modules table
  tags?: string[] | null;
  // Engine type + built-in status
  engine_type?: DeploymentEngineType;
  lifecycle_capabilities?: LifecycleCapabilities;
  is_builtin?: boolean;
  module_type?: 'manifest' | 'helm_chart' | 'terraform' | 'opentofu' | 'ansible' | 'script';
}

export interface ModuleVariable {
  name: string;
  type: string;
  description?: string;
  default?: JsonValue;
  required: boolean;
  sensitive: boolean;
}

export interface ProjectModule {
  id: number;
  project_id: number;
  module_library_id: number;
  module_name: string;
  name?: string; // Alias for module_name in some contexts
  category?: string;
  provider?: string;
  path_in_project: string;
  variable_overrides?: Record<string, JsonValue>;
  deployment_order: number;
  enabled: boolean;
  status: string;
  last_deployed_at?: string;
  deployment_error?: string;
  git_source?: string;
  library_module?: ModuleLibrary;
  dependencies?: number[];
  outputs?: Record<string, JsonValue>; // Captured OpenTofu outputs after successful apply
  // Stack association (optional - only set if module was created from a stack)
  stack_instance_id?: number;
  // State-related fields (populated from state queries)
  state_serial?: number;
  resource_count?: number;
  state_size?: number;
  updated_at?: string;
  // Persistent workspace fields (WORK-001)
  workspace_path?: string;
  last_init_at?: string;
  plan_output?: string;
  plan_serial?: number;
  /** Fine-grained execution stage (e.g. "Executing command 2/5...") — from backend poll */
  stage_detail?: string | null;
  vars_hash?: string;
  engine_type?: DeploymentEngineType;
  lifecycle_capabilities?: LifecycleCapabilities;
  /** True if this module may disrupt SSH connectivity during execution */
  connectivity_risk?: boolean;
  /** Phase grouping for SSH modules (e.g. "Discovery", "DPU Flash", "Cluster Init") */
  phase?: string;
}

// Plan Status Response (WORK-007)
export interface PlanStatus {
  module_id: number;
  has_plan: boolean;
  plan_valid: boolean;
  plan_serial: number;
  plan_age_seconds?: number;
  reason_invalid: string;
  workspace_initialized: boolean;
  last_init_at?: string;
  vars_hash?: string;
  plan_output_preview?: string;
}

export interface AddModuleRequest {
  module_library_id: number;
  path_in_project: string;
  variable_overrides?: Record<string, JsonValue>;
  deployment_order?: number;
}

// Module sync stats
export interface ModuleSyncStats {
  created: number;
  updated: number;
  unchanged: number;
  errors: string[];
  total_modules?: number;
  modules_found?: number;
  modules_created?: number;
  modules_updated?: number;
  synced_version?: string;
}
