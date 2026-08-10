// Config Export/Import/Diff types

export interface ConfigExport {
  bnk_forge_export: {
    version: string;
    exported_at: string;
    cluster: { name: string; id: number; k8s_version: string; api_server: string; cloud_provider: string };
    project: { name: string; id: number | null; project_type: string | null };
    export_metadata: { resource_counts: Record<string, number>; total_resources: number; categories: string[]; duration_ms: number };
  };
  resources: Record<string, unknown[]>;
  module_config: Record<string, { variables: Record<string, unknown>; status: string; outputs: Record<string, unknown> }>;
}

export interface ConfigImportResult {
  message: string;
  results: {
    applied: Array<{ kind: string; name: string; namespace: string }>;
    failed: Array<{ kind: string; name: string; namespace: string; error: string }>;
    skipped: Array<{ kind: string; name: string; namespace: string; reason: string }>;
  };
  target_cluster: string;
}

export interface ConfigDiffResult {
  summary: string;
  total_diffs: number;
  cluster_a: string;
  cluster_b: string;
  resources: {
    only_in_a: string[];
    only_in_b: string[];
    changed: Array<{ resource: string; kind: string; spec_a: Record<string, unknown>; spec_b: Record<string, unknown> }>;
  };
  modules: {
    only_in_a: string[];
    only_in_b: string[];
    changed: Array<{ module_path: string; variables_a: Record<string, unknown>; variables_b: Record<string, unknown> }>;
  };
}
