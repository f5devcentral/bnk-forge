export interface BlueprintSource {
  id: number;
  name: string;
  source_type: 'git' | 'builtin';
  url: string;
  branch?: string | null;
  git_ref?: string | null;
  sync_status: 'pending' | 'syncing' | 'success' | 'failed';
  sync_error?: string | null;
  last_synced_at?: string | null;
  release_count: number;
  is_active: boolean;
  is_default: boolean;
  description?: string | null;
  created_at: string;
  updated_at: string;
}

export interface BlueprintSourceCreate {
  name: string;
  source_type: 'git' | 'builtin';
  url: string;
  branch?: string;
  git_ref?: string;
  is_active?: boolean;
  description?: string;
  make_default?: boolean;
}

export interface BlueprintSourceUpdate {
  name?: string;
  url?: string;
  branch?: string;
  git_ref?: string;
  is_active?: boolean;
  description?: string;
  make_default?: boolean;
}

export interface BlueprintRelease {
  id: number;
  blueprint_source_id: number;
  source_name?: string | null;
  source_type?: string | null;
  blueprint_id: string;
  blueprint_version: string;
  blueprint_name: string;
  blueprint_description?: string | null;
  category?: string | null;
  cloud_provider?: string | null;
  bnk_version?: string | null;
  tags?: string[];
  schema_version: number;
  source_path?: string | null;
  source_ref?: string | null;
  content_sha256: string;
  validation_state: string;
  validation_errors?: Array<Record<string, unknown>> | null;
  release_state: string;
  state_reason?: string | null;
  is_active: boolean;
  imported_at?: string | null;
  created_at: string;
  updated_at: string;
  deployed_project_count?: number;
  is_featured: boolean;
  mutable_lifecycle_fields: string[];
}

export interface BlueprintSourceSyncResult {
  success: boolean;
  source_id: number;
  source_name: string;
  sync_status: string;
  results: {
    blueprints_found: number;
    releases_created: number;
    releases_existing: number;
    releases_conflicted?: number;
    releases_invalid: number;
    errors: string[];
  };
}
