/**
 * Projects & Modules API methods
 */
import { apiClient } from './client';
import type {
  Project,
  ProjectCreateData,
  ProjectUpdateData,
  ProjectModule,
  AddModuleRequest,
  ModuleLibrary,
  ModuleVariable,
  JsonValue,
  ExecutionPlan,
  DeployAllResponse,
  RunProgress,
  ParallelExecutionStatus,
  ParallelExecutionSummary,
  ProjectSecret,
  ValueSecretCreate,
  ValueSecretUpdate,
  RequiredSecretsResponse,
} from '@/types';
import type { ModuleSyncStats } from '@/types';
import type {
  ApiProjectCreate,
  ApiProjectUpdate,
  ApiAddModuleRequest,
  ApiValueSecretCreate,
  ApiValueSecretUpdate,
  AssertKeysMatch,
} from '@/types/api-schemas';
import type { components } from '@/types/api-generated';

// Module actions (D-034 — manifest-declared test/scenario actions)
export type ModuleActionInputDef = components['schemas']['ModuleActionInputDef'];
export type ModuleActionInfo = components['schemas']['ModuleActionInfo'];
export type ModuleActionsListResponse = components['schemas']['ModuleActionsListResponse'];
export type ModuleActionSubmitResponse = components['schemas']['ModuleActionSubmitResponse'];

// Module reports (D-034 PR-2.5 — tool-written run/scenario reports)
export type ModuleReportFile = components['schemas']['ModuleReportFile'];
export type ModuleReportRun = components['schemas']['ModuleReportRun'];
export type ModuleReportsListResponse = components['schemas']['ModuleReportsListResponse'];
export type ModuleReportContentResponse = components['schemas']['ModuleReportContentResponse'];

// ── Compile-time contract checks ────────────────────────────────────────────
// These verify that frontend types don't send keys the backend doesn't expect.
// If a key mismatch exists, `tsc --noEmit` will fail with a clear error.
// The `as const` trick ensures these are truly compile-time-only (no runtime cost).
const _checkProjectCreate: AssertKeysMatch<ProjectCreateData, ApiProjectCreate> = true;
const _checkProjectUpdate: AssertKeysMatch<ProjectUpdateData, ApiProjectUpdate> = true;
const _checkAddModule: AssertKeysMatch<AddModuleRequest, ApiAddModuleRequest> = true;
const _checkValueSecretCreate: AssertKeysMatch<ValueSecretCreate, ApiValueSecretCreate> = true;
const _checkValueSecretUpdate: AssertKeysMatch<ValueSecretUpdate, ApiValueSecretUpdate> = true;

// Suppress unused variable warnings
// eslint-disable-next-line @typescript-eslint/no-unused-expressions
void _checkProjectCreate, _checkProjectUpdate, _checkAddModule, _checkValueSecretCreate, _checkValueSecretUpdate;

export const projectsApi = {
  // Projects
  getProjects: () =>
    apiClient.get<{ projects: Project[]; total: number }>('/api/projects').then((res) => res.data.projects),

  getProject: (id: number) =>
    apiClient.get<Project>(`/api/projects/${id}`).then((res) => res.data),

  createProject: (data: ProjectCreateData) =>
    apiClient.post<{ success: boolean; project_id: number; name: string; message: string }>(
      '/api/projects',
      data
    ).then((res) => res.data),

  updateProject: (id: number, data: ProjectUpdateData) =>
    apiClient.put(`/api/projects/${id}`, data).then((res) => res.data),

  deleteProject: (id: number) =>
    apiClient.delete(`/api/projects/${id}`).then((res) => res.data),

  /** MU-009: Transfer project ownership to another user */
  transferOwnership: (projectId: number, newOwnerId: number) =>
    apiClient.post<{ success: boolean; project_id: number; project_name: string; previous_owner: string | null; new_owner: string; message: string }>(
      `/api/projects/${projectId}/transfer`,
      { new_owner_id: newOwnerId }
    ).then((res) => res.data),

  updateProjectDependencies: (id: number, dependencies: Array<{ project_id: number; outputs: string[] }>) =>
    apiClient.put(`/api/projects/${id}/dependencies`, { dependencies }).then((res) => res.data),

  // Modules
  getProjectModules: (projectId: number) =>
    apiClient.get<{ modules: ProjectModule[]; total: number }>(`/api/project-modules/project/${projectId}`).then((res) => res.data.modules),

  getAllModules: () =>
    apiClient.get<{ items: Array<{ id: number; module_name: string; status: string; last_deployed_at: string | null; project_id: number; project_name: string | null; path_in_project: string; deployment_error: string | null; library_module: { id: number; name: string; category: string; path: string } | null }>; pagination: { page: number; page_size: number; total_items: number; total_pages: number; has_next: boolean; has_prev: boolean } }>('/api/projects/modules/all?page_size=200').then((res) => res.data.items),

  addModuleToProject: (projectId: number, data: AddModuleRequest) =>
    apiClient.post(`/api/project-modules/project/${projectId}/add`, data).then((res) => res.data),

  updateModule: (moduleId: number, data: Partial<ProjectModule>) =>
    apiClient.put(`/api/project-modules/${moduleId}`, data).then((res) => res.data),

  changeModuleVersion: (moduleId: number, targetVersion: string) =>
    apiClient
      .post<{
        success: boolean;
        message: string;
        module_id: number;
        path: string;
        previous_version: string | null;
        version: string;
        module_library_id: number;
      }>(`/api/project-modules/${moduleId}/change-version`, { target_version: targetVersion })
      .then((res) => res.data),

  deleteModule: (moduleId: number) =>
    apiClient.delete(`/api/project-modules/${moduleId}`).then((res) => res.data),

  // Module operations
  initModule: (moduleId: number) =>
    apiClient.post(`/api/project-modules/${moduleId}/init`).then((res) => res.data),

  planModule: (moduleId: number) =>
    apiClient.post(`/api/project-modules/${moduleId}/plan`).then((res) => res.data),

  applyModule: (moduleId: number, autoApprove: boolean = false) =>
    apiClient.post(`/api/project-modules/${moduleId}/apply`, { auto_approve: autoApprove }).then((res) => res.data),

  destroyModule: (moduleId: number, autoApprove: boolean = false) =>
    apiClient.post(`/api/project-modules/${moduleId}/destroy`, { auto_approve: autoApprove }).then((res) => res.data),

  getModuleStatus: (moduleId: number) =>
    apiClient.get(`/api/project-modules/${moduleId}/status`).then((res) => res.data),

  getModuleStateHistory: (moduleId: number, limit: number = 50) =>
    apiClient
      .get<{
        module_id: number;
        count: number;
        transitions: Array<{
          id: number;
          from_status: string;
          to_status: string;
          task_id: number | null;
          fence_token: number | null;
          reason: string | null;
          at: string;
        }>;
      }>(`/api/project-modules/${moduleId}/state-history?limit=${limit}`)
      .then((res) => res.data),

  cancelDeployment: (moduleId: number) =>
    apiClient.post(`/api/project-modules/${moduleId}/cancel`).then((res) => res.data),

  recoverState: (moduleId: number) =>
    apiClient.post(`/api/project-modules/${moduleId}/recover-state`).then((res) => res.data),

  rerunModule: (moduleId: number) =>
    apiClient.post(`/api/project-modules/${moduleId}/rerun`).then((res) => res.data),

  // Module actions (D-034 — manifest-declared test/scenario actions)
  getModuleActions: (moduleId: number) =>
    apiClient
      .get<ModuleActionsListResponse>(`/api/project-modules/${moduleId}/actions`)
      .then((res) => res.data),

  runModuleAction: (moduleId: number, actionName: string, inputs?: Record<string, unknown>) =>
    apiClient
      .post<ModuleActionSubmitResponse>(
        `/api/project-modules/${moduleId}/actions/${encodeURIComponent(actionName)}`,
        { inputs: inputs ?? null }
      )
      .then((res) => res.data),

  // Module reports (D-034 PR-2.5 — tool-written run/scenario reports)
  getModuleReports: (moduleId: number) =>
    apiClient
      .get<ModuleReportsListResponse>(`/api/project-modules/${moduleId}/reports`)
      .then((res) => res.data),

  getModuleReportContent: (moduleId: number, path: string) =>
    apiClient
      .get<ModuleReportContentResponse>(`/api/project-modules/${moduleId}/reports/content`, {
        params: { path },
      })
      .then((res) => res.data),

  // Module Library
  getModuleLibrary: (params?: { category?: string; provider?: string; search?: string }) =>
    apiClient.get<{ modules: ModuleLibrary[]; total: number }>('/api/module-library', { params }).then((res) => res.data.modules),

  getModuleDetails: (moduleId: number) =>
    apiClient.get<ModuleLibrary>(`/api/module-library/${moduleId}`).then((res) => res.data),

  getModuleVariables: (moduleId: number) =>
    apiClient.get<{ success: boolean; variables: ModuleVariable[] }>(`/api/module-library/${moduleId}/variables`).then((res) => res.data.variables),

  getSmartDefaults: (moduleId: number, projectId: number) =>
    apiClient.get<{ success: boolean; defaults: Record<string, JsonValue> }>(`/api/module-library/${moduleId}/smart-defaults`, { params: { project_id: projectId } }).then((res) => res.data.defaults),

  getModuleCategories: () =>
    apiClient.get<{ categories: Array<{ name: string; count: number }> }>('/api/module-library/categories').then((res) => res.data.categories),

  getModuleProviders: () =>
    apiClient.get<{ providers: Array<{ name: string; count: number }> }>('/api/module-library/providers').then((res) => res.data.providers),

  syncModuleLibrary: (force = false) =>
    apiClient.post<{ success: boolean; message: string; stats: ModuleSyncStats }>('/api/module-library/sync', null, { params: { force } }).then((res) => res.data),

  getModuleLibraryVersion: () =>
    apiClient.get<{
      synced_version: string | null;
      latest_version: string | null;
      update_available: boolean;
      last_synced_at: string | null;
      git_ref: string | null;
      error: string | null;
    }>('/api/module-library/version').then((res) => res.data),

  // State Metadata
  getModuleStateInfo: (moduleId: number) =>
    apiClient.get<{
      module_id: number;
      module_name: string;
      state_exists: boolean;
      state_location: string;
      status: string;
      last_deployed_at: string | null;
      lineage?: string;
      serial?: number;
      terraform_version?: string;
      resources_count?: number;
      state_modified_at?: string;
      error?: string;
    }>(`/api/project-modules/${moduleId}/state-info`).then((res) => res.data),

  // State Viewer
  getModuleState: (moduleId: number) =>
    apiClient.get(`/api/state/module/${moduleId}`).then((res) => res.data),

  getModuleResources: (moduleId: number) =>
    apiClient.get(`/api/state/module/${moduleId}/resources`).then((res) => res.data),

  getResourceDetails: (moduleId: number, resourceAddress: string) =>
    apiClient.get(`/api/state/module/${moduleId}/resource/${encodeURIComponent(resourceAddress)}`).then((res) => res.data),

  getDependencyGraph: (moduleId: number) =>
    apiClient.get(`/api/state/module/${moduleId}/graph`).then((res) => res.data),

  getModuleOutputs: (moduleId: number) =>
    apiClient.get(`/api/state/module/${moduleId}/outputs`).then((res) => res.data),

  refreshModuleState: (moduleId: number) =>
    apiClient.post(`/api/state/module/${moduleId}/refresh`).then((res) => res.data),

  getRawState: (moduleId: number) =>
    apiClient.get(`/api/state/module/${moduleId}/raw`).then((res) => res.data),

  getProjectModulesWithState: (projectId: number) =>
    apiClient.get(`/api/state/project/${projectId}/modules`).then((res) => res.data),

  // Module Logs
  getModuleLogs: (moduleId: number, params?: { limit?: number; level?: string }) =>
    apiClient.get(`/api/project-modules/${moduleId}/logs`, { params }).then((res) => res.data),

  // Deployment History
  getModuleDeployments: (moduleId: number, params?: { action?: string; status?: string; limit?: number }) =>
    apiClient.get(`/api/project-modules/${moduleId}/deployments`, { params }).then((res) => res.data),

  getProjectDeployments: (projectId: number, params?: { action?: string; status?: string; limit?: number }) =>
    apiClient.get(`/api/project-modules/project/${projectId}/deployments`, { params }).then((res) => res.data),

  // Snapshots & Rollback
  getModuleSnapshots: (moduleId: number) =>
    apiClient.get(`/api/snapshots/module/${moduleId}`).then((res) => res.data),

  createModuleSnapshot: (moduleId: number, data: { description?: string; created_by: string }) =>
    apiClient.post(`/api/snapshots/module/${moduleId}/create`, data).then((res) => res.data),

  rollbackToSnapshot: (moduleId: number, snapshotId: number, data: { auto_approve?: boolean }) =>
    apiClient.post(`/api/snapshots/module/${moduleId}/rollback/${snapshotId}`, data).then((res) => res.data),

  deleteSnapshot: (snapshotId: number) =>
    apiClient.delete(`/api/snapshots/${snapshotId}`).then((res) => res.data),

  // Variable Defaults
  getVariableDefaults: (projectId: number) =>
    apiClient.get<{
      project_id: number;
      project_name: string;
      common_defaults: Record<string, JsonValue>;
      custom_defaults: Record<string, JsonValue>;
      effective_defaults: Record<string, JsonValue>;
    }>(`/api/projects/${projectId}/variable-defaults`).then((res) => res.data),

  updateVariableDefaults: (projectId: number, defaults: Record<string, JsonValue>) =>
    apiClient.put<{ success: boolean; message: string; defaults_count: number }>(
      `/api/projects/${projectId}/variable-defaults`,
      { defaults }
    ).then((res) => res.data),

  resetVariableDefaults: (projectId: number) =>
    apiClient.delete<{ success: boolean; message: string }>(`/api/projects/${projectId}/variable-defaults`).then((res) => res.data),

  // Project Variables (replaces Variable Defaults)
  getProjectVariables: (projectId: number) =>
    apiClient.get<{
      project_id: number;
      project_name: string;
      variables: Record<string, JsonValue>;
      discovered: Record<string, { 
        value: JsonValue; 
        display_value?: JsonValue;
        type: string;
        source: string; 
        source_module?: string;
        auto_discovered: boolean;
        is_truncated?: boolean;
      }>;
      configurable: Record<string, {
        type: string;
        description: string;
        default: JsonValue;
        required: boolean;
        sensitive: boolean;
        used_by: string[];
        modules_count: number;
        current_value: JsonValue;
        value_source: 'user' | 'discovered' | 'default' | 'unset';
        is_auto_wired?: boolean;
        defined_in?: string;
      }>;
      modules: Record<string, {
        path: string;
        status: string;
        deployment_order: number;
        variables: Array<{
          name: string;
          type: string;
          description: string;
          default: JsonValue;
          required: boolean;
          sensitive: boolean;
          is_auto_wired: boolean;
        }>;
      }>;
      effective: Record<string, JsonValue>;
    }>(`/api/projects/${projectId}/variables`).then((res) => res.data),

  updateProjectVariables: (projectId: number, variables: Record<string, JsonValue>) =>
    apiClient.put<{ success: boolean; message: string; variables_count: number; variables: Record<string, JsonValue> }>(
      `/api/projects/${projectId}/variables`,
      { variables }
    ).then((res) => res.data),

  // P2-3: Parallel Execution
  fetchExecutionPlan: (projectId: number) =>
    apiClient.get<ExecutionPlan>(`/api/projects/${projectId}/execution-plan`).then((res) => res.data),

  deployAllModules: (projectId: number, parallel: boolean = true) =>
    apiClient.post<DeployAllResponse>(`/api/projects/${projectId}/deploy-all`, { parallel }).then((res) => res.data),

  destroyAllModules: (projectId: number, parallel: boolean = true) =>
    apiClient.post<DeployAllResponse>(`/api/projects/${projectId}/destroy-all`, { parallel }).then((res) => res.data),

  fetchRunProgress: (projectId: number, runHandle: string) =>
    apiClient.get<RunProgress>(`/api/projects/${projectId}/orchestration/${runHandle}`).then((res) => res.data),

  fetchParallelExecutionStatus: (projectId: number, executionId: number) =>
    apiClient.get<ParallelExecutionStatus>(`/api/projects/${projectId}/parallel-executions/${executionId}`).then((res) => res.data),

  fetchParallelExecutions: (projectId: number, limit: number = 10) =>
    apiClient.get<ParallelExecutionSummary[]>(`/api/projects/${projectId}/parallel-executions`, { params: { limit } }).then((res) => res.data),

  // Project Secrets
  listProjectSecrets: (projectId: number) =>
    apiClient.get<{ success: boolean; secrets: ProjectSecret[]; count: number }>(`/api/projects/${projectId}/secrets`).then((res) => res.data),

  createFileSecret: async (
    projectId: number,
    file: File,
    name: string,
    description?: string,
    targetModulePath?: string,
    targetVariableName?: string
  ) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('name', name);
    if (description) formData.append('description', description);
    if (targetModulePath) formData.append('target_module_path', targetModulePath);
    if (targetVariableName) formData.append('target_variable_name', targetVariableName);
    return apiClient.post<{ success: boolean; secret: ProjectSecret }>(`/api/projects/${projectId}/secrets/file`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((res) => res.data);
  },

  createValueSecret: (projectId: number, data: ValueSecretCreate) =>
    apiClient.post<{ success: boolean; secret: ProjectSecret }>(`/api/projects/${projectId}/secrets/value`, data).then((res) => res.data),

  importSpecialSecret: async (
    projectId: number,
    secretName: 'cne_pull_secret' | 'jwt_token',
    file: File,
  ) => {
    const formData = new FormData();
    formData.append('secret_name', secretName);
    formData.append('file', file);
    return apiClient.post<{ success: boolean; action: 'created' | 'updated'; secret: ProjectSecret }>(
      `/api/projects/${projectId}/secrets/import`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    ).then((res) => res.data);
  },

  updateValueSecret: (projectId: number, secretId: number, data: ValueSecretUpdate) =>
    apiClient.put<{ success: boolean; secret: ProjectSecret }>(`/api/projects/${projectId}/secrets/${secretId}/value`, data).then((res) => res.data),

  deleteProjectSecret: (projectId: number, secretId: number) =>
    apiClient.delete<{ success: boolean }>(`/api/projects/${projectId}/secrets/${secretId}`).then((res) => res.data),

  getRequiredSecrets: (projectId: number, stackSlug?: string) =>
    apiClient.get<RequiredSecretsResponse>(`/api/projects/${projectId}/secrets/required`, { params: stackSlug ? { stack_slug: stackSlug } : {} }).then((res) => res.data),

  // Stack variable secrets (wizard-provided values stored in stack_instance.variables)
  updateStackVariableSecret: (projectId: number, name: string, value: string) =>
    apiClient.put<{ success: boolean; name: string }>(`/api/projects/${projectId}/secrets/stack-variable`, { name, value }).then((res) => res.data),

  deleteStackVariableSecret: (projectId: number, name: string) =>
    apiClient.delete<{ success: boolean }>(`/api/projects/${projectId}/secrets/stack-variable/${encodeURIComponent(name)}`).then((res) => res.data),
};
