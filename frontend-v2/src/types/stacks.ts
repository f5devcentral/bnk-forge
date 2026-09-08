// Stack Template & Instance types

import type { JsonValue } from './common';
import type { DeploymentEngineType, LifecycleCapabilities } from './modules';

export interface StackTemplateModule {
  path: string;
  name: string;
  required: boolean;
  description?: string;
  variables: Record<string, JsonValue>;
  /** D-033: the module version the blueprint pins (null for legacy manifests). */
  version?: string | null;
  engine_type?: DeploymentEngineType;
  lifecycle_capabilities?: LifecycleCapabilities;
  module_catalog_status?: 'available' | 'missing';
  module_catalog_message?: string;
  /** True if this module may disrupt SSH connectivity during execution */
  connectivity_risk?: boolean;
}

export interface StackPrerequisite {
  type: string;
  name?: string;
  description: string;
}

/** S19-001: Result of checking stack prerequisites against a project */
export interface StackPrerequisiteSecretStatus {
  name: string;
  description: string;
  exists: boolean;
  source?: 'project' | 'stack_variable' | 'system_default' | null;
  valid?: boolean;
  validation_error?: string;
  validated_source?: 'project' | 'stack_variable' | 'system_default';
  guidance?: string;
}

export interface StackPreflightCheck {
  id: string;
  title: string;
  status: 'pass' | 'warn' | 'fail';
  blocking: boolean;
  message: string;
  guidance?: string;
}

export interface StackPrerequisitesCheck {
  all_satisfied: boolean;
  missing_secrets: StackPrerequisiteSecretStatus[];
  required_secrets: StackPrerequisiteSecretStatus[];
  preflight_checks?: StackPreflightCheck[];
  secret_source_policy?: {
    project_secret_precedence: boolean;
    global_cne_pull_secret_default_supported: boolean;
  };
}

export interface StackTemplate {
  id: number;
  name: string;
  slug: string;
  description?: string;
  category?: string;
  cloud_provider?: string;
  icon?: string;
  color?: string;
  estimated_time?: string;
  estimated_cost?: string;
  difficulty?: string;
  modules?: StackTemplateModule[];
  variable_templates?: Record<string, JsonValue>;
  prerequisites?: StackPrerequisite[];
  tags?: string[];
  maturity?: string;
  outcomes?: string[];
  platform_defaults?: Record<string, Record<string, unknown>> | null;
  bnk_version?: string | null;
  is_active: boolean;
  is_featured: boolean;
  // Phase 4: Versioning and custom stacks
  version?: string;
  created_by?: string;
  is_public?: boolean;
  forked_from?: number;
  source_kind?: 'stack_template' | 'blueprint_release';
  blueprint_release_id?: number;
  blueprint_source_id?: number;
  release_state?: string;
  validation_state?: string;
  source_path?: string;
  created_at: string;
  updated_at: string;
}

/** Discriminant for deploy-path routing in Stacks.tsx */
export type StackDeployKind = 'builtin-release' | 'git-release' | 'template';

export interface StackTemplateList {
  id: number;
  name: string;
  slug: string;
  description?: string;
  category?: string;
  cloud_provider?: string;
  icon?: string;
  color?: string;
  estimated_time?: string;
  estimated_cost?: string;
  difficulty?: string;
  tags?: string[];
  maturity?: string;
  is_active: boolean;
  is_featured: boolean;
  platform_defaults?: Record<string, Record<string, unknown>> | null;
  bnk_version?: string | null;
  // Phase 4: Versioning and custom stacks
  version?: string;
  created_by?: string;
  is_public?: boolean;
  forked_from?: number;
  source_kind?: 'stack_template' | 'blueprint_release';
  blueprint_release_id?: number;
  blueprint_source_id?: number;
  release_state?: string;
  validation_state?: string;
  source_path?: string;
  /** P4: routing discriminant — determines which deploy dialog opens */
  deploy_kind?: StackDeployKind;
  /** P4: for builtin-release items, the template slug to open in StackDetailDialog */
  deploy_slug?: string;
}

export interface StackPreview {
  modules: StackTemplateModule[];
  prerequisites?: StackPrerequisite[];
  estimated_cost?: string;
  estimated_time?: string;
  total_modules: number;
}

export interface StackInstance {
  id: number;
  project_id: number;
  template_id: number;
  template_slug?: string | null;
  name: string;
  status: string; // "pending", "deploying", "deployed", "failed", "destroying", "destroyed"
  variables?: Record<string, JsonValue>;
  deployed_modules: number[];
  current_step: number;
  total_steps?: number;
  started_at?: string;
  completed_at?: string;
  error_message?: string;
  created_at: string;
  updated_at: string;
}

export interface StackInstanceCreate {
  template_id: number;
  name: string;
  variables?: Record<string, JsonValue>;
}

export interface VariableValidation {
  pattern?: string;
  error_message?: string;
  type?: 'file_path' | 'regex' | 'range';  // Validation type hint
  min?: number;
  max?: number;
}

export interface StackInputDefinition {
  name: string;
  type: string;
  description: string;
  example?: string;
  default?: string;
  module_path: string;
  module_name: string;
  required: boolean;
  sensitive?: boolean;
  source?: string;
  source_field?: string;
  validation?: VariableValidation;
  stack_default?: boolean; // True if value comes from stack template
  /**
   * Author-controlled render order in the deploy form. Lower values come
   * first; inputs without an order render after all explicitly-ordered
   * ones in their original manifest sequence.
   */
  order?: number | null;
  options?: Array<{
    label: string;
    value: string;
    description?: string;
  }>;
  /** True when forge resolves this input automatically from project context (credential_template, project, project_secret, module). */
  hidden?: boolean;
  /** Which source category forge resolves this from, or null if user-supplied. */
  resolved_from?: string | null;
}

export interface StackInputGroupSummary {
  label: string;
  value: string;
}

export interface StackRequiredInputs {
  template_slug: string;
  template_name: string;
  inputs_by_module: Record<string, StackInputDefinition[]>;
  all_inputs: StackInputDefinition[];
  total_required: number;
  total_optional: number;
  missing_modules?: Array<{
    path: string;
    name: string;
    message: string;
  }>;
  summary?: StackInputGroupSummary[];
}

export interface StackModuleStatus {
  id: number;
  name: string;
  path: string;
  status: string;
  deployment_order: number;
  error?: string;
}

export interface StackStatus {
  status: string;
  current_step: number;
  total_steps: number;
  modules: StackModuleStatus[];
  progress_percentage?: number;
  started_at?: string;
  completed_at?: string;
  error_message?: string;
}

// Phase 4: Custom stacks request types
export interface SaveAsTemplateRequest {
  name: string;
  description?: string;
  category?: string;
  version?: string;
  is_public?: boolean;
  tags?: string[];
  icon?: string;
  color?: string;
}

export interface DuplicateTemplateRequest {
  name: string;
  description?: string;
  version?: string;
}

export interface PublishTemplateRequest {
  is_public: boolean;
}

export interface ImportedBlueprintProjectCreateResponse {
  success: boolean;
  project_id: number;
  project_name: string;
  blueprint_release_id: number;
  module_count: number;
  created_module_ids: number[];
  message: string;
}
