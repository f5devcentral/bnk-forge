// Project Secrets types

export interface ProjectSecret {
  id: number;
  name: string;
  description?: string;
  secret_type: 'file' | 'value';
  target_module_path?: string;
  target_variable_name?: string;
  // File-specific fields
  filename?: string;
  file_size?: number;
  mime_type?: string;
  // Timestamps
  created_at?: string;
  updated_at?: string;
  last_used_at?: string;
}

export interface ProjectSecretCreate {
  name: string;
  description?: string;
  target_module_path?: string;
  target_variable_name?: string;
}

export interface ValueSecretCreate extends ProjectSecretCreate {
  value: string;
}

export interface ValueSecretUpdate {
  value?: string;
  description?: string;
  target_module_path?: string;
  target_variable_name?: string;
}

export interface RequiredSecret {
  name: string;
  type: 'file' | 'value';
  description?: string;
  module_path: string;
  variable_name: string;
  required: boolean;
  exists: boolean;
  secret_id?: number;
  source?: string | null; // "project" | "stack_variable" | null
}

export interface RequiredSecretsResponse {
  success: boolean;
  required_secrets: RequiredSecret[];
  total_required: number;
  missing_count: number;
  all_satisfied: boolean;
  secret_source_policy?: {
    project_secret_precedence: boolean;
    global_cne_pull_secret_default_supported: boolean;
  };
}
