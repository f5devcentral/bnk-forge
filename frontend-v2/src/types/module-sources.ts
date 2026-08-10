// Module Source, Registry & Variable Mapping types

// Variable Mapping Types
export interface VariableMapping {
  id: number;
  source_variable_name: string;
  target_variable_name: string;
  transform_type: string; // none, uppercase, lowercase, json_encode, base64
  is_active: boolean;
  description?: string;
  created_at?: string;
  updated_at?: string;
}

export interface VariableMappingRequest {
  source_variable_name: string;
  target_variable_name: string;
  transform_type?: string;
  is_active?: boolean;
  description?: string;
}

export interface VariableMappingTemplate {
  id: number;
  name: string;
  description?: string;
  mappings: Array<{
    source: string;
    target: string;
    transform: string;
  }>;
  usage_count: number;
  is_public: boolean;
  created_at?: string;
}

export interface VariableMappingTemplateRequest {
  name: string;
  description?: string;
  mappings: Array<{
    source: string;
    target: string;
    transform: string;
  }>;
  is_public?: boolean;
}

// Module Source Types (P2-1)
export interface ModuleSource {
  id: number;
  name: string;
  source_type: 'git' | 'registry' | 'builtin';
  url: string;
  branch?: string;
  git_ref?: string;
  auth_type?: 'none' | 'token' | 'ssh' | 'basic';
  last_synced_at?: string;
  sync_status: 'pending' | 'syncing' | 'success' | 'failed';
  sync_error?: string;
  module_count: number;
  is_active: boolean;
  is_default: boolean;
  auto_sync: boolean;
  sync_interval_hours: number;
  description?: string;
  created_at: string;
  updated_at: string;
}

export interface ModuleSourceCreate {
  name: string;
  source_type: 'git' | 'registry' | 'builtin';
  url: string;
  branch?: string;
  git_ref?: string;
  auth_type?: 'none' | 'token' | 'ssh' | 'basic';
  auth_token?: string;
  auto_sync?: boolean;
  sync_interval_hours?: number;
  description?: string;
  make_default?: boolean;
}

export interface ModuleSourceUpdate {
  name?: string;
  url?: string;
  branch?: string;
  git_ref?: string;
  auth_type?: 'none' | 'token' | 'ssh' | 'basic';
  auth_token?: string;
  auto_sync?: boolean;
  sync_interval_hours?: number;
  is_active?: boolean;
  description?: string;
  make_default?: boolean;
}

export interface ModuleSyncResult {
  success: boolean;
  source_id: number;
  source_name: string;
  sync_status: string;
  results: {
    modules_found: number;
    modules_created: number;
    modules_updated: number;
    errors: string[];
  };
}

// Registry Types (P2-1 Phase 2)
export interface RegistryModule {
  id: string;
  namespace: string;
  name: string;
  provider: string;
  description: string;
  source: string;
  published_at: string;
  downloads: number;
  verified: boolean;
  version: string;
  versions?: string[];
}

export interface RegistrySearchResponse {
  modules: RegistryModule[];
  meta: {
    limit: number;
    offset: number;
    total_count: number;
  };
  source: 'opentofu' | 'terraform' | 'none';
}

export interface RegistryModuleImport {
  namespace: string;
  name: string;
  provider: string;
  version?: string;
  use_terraform_registry?: boolean;
}
