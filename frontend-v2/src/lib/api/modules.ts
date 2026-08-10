/**
 * Module Sources & Registry API methods
 */
import { apiClient } from './client';
import type {
  ModuleSource,
  ModuleSourceCreate,
  ModuleSourceUpdate,
  ModuleSyncResult,
  ModuleLibrary,
  RegistryModule,
  RegistrySearchResponse,
  RegistryModuleImport,
  VariableMapping,
  VariableMappingRequest,
  VariableMappingTemplate,
  VariableMappingTemplateRequest,
} from '@/types';

export const modulesApi = {
  // Module Sources (P2-1)
  getModuleSources: (params?: { source_type?: string; is_active?: boolean }) =>
    apiClient.get<ModuleSource[]>('/api/module-sources', { params }).then((res) => res.data),

  getModuleSource: (sourceId: number) =>
    apiClient.get<ModuleSource>(`/api/module-sources/${sourceId}`).then((res) => res.data),

  createModuleSource: (data: ModuleSourceCreate) =>
    apiClient.post<ModuleSource>('/api/module-sources', data).then((res) => res.data),

  updateModuleSource: (sourceId: number, data: ModuleSourceUpdate) =>
    apiClient.put<ModuleSource>(`/api/module-sources/${sourceId}`, data).then((res) => res.data),

  deleteModuleSource: (sourceId: number) =>
    apiClient.delete<{ success: boolean; message: string }>(`/api/module-sources/${sourceId}`).then((res) => res.data),

  syncModuleSource: (sourceId: number) =>
    apiClient.post<ModuleSyncResult>(`/api/module-sources/${sourceId}/sync`).then((res) => res.data),

  getSourceModules: (sourceId: number) =>
    apiClient.get<{ source_id: number; source_name: string; module_count: number; modules: ModuleLibrary[] }>(
      `/api/module-sources/${sourceId}/modules`
    ).then((res) => res.data),

  // Registry (P2-1 Phase 2)
  searchRegistry: (params?: { q?: string; provider?: string; verified?: boolean; limit?: number; offset?: number }) =>
    apiClient.get<RegistrySearchResponse>('/api/registry/search', { params }).then((res) => res.data),

  getRegistryModule: (namespace: string, name: string, provider: string, useTerraformRegistry?: boolean) =>
    apiClient.get<RegistryModule>(
      `/api/registry/modules/${namespace}/${name}/${provider}`,
      { params: { use_terraform_registry: useTerraformRegistry } }
    ).then((res) => res.data),

  getRegistryModuleVersions: (namespace: string, name: string, provider: string, useTerraformRegistry?: boolean) =>
    apiClient.get<{ namespace: string; name: string; provider: string; versions: Array<{ version: string; published_at?: string }> }>(
      `/api/registry/modules/${namespace}/${name}/${provider}/versions`,
      { params: { use_terraform_registry: useTerraformRegistry } }
    ).then((res) => res.data),

  importRegistryModule: (data: RegistryModuleImport) =>
    apiClient.post<{ success: boolean; message: string; module: ModuleLibrary }>('/api/registry/import', data).then((res) => res.data),

  importRegistryGit: (data: { url: string; ref: string; subpath?: string; version_label?: string; display_name?: string }) =>
    apiClient.post<{ success: boolean; message: string; module: ModuleLibrary }>('/api/registry/import-git', data).then((res) => res.data),

  importRegistryTFC: (data: { hostname: string; namespace: string; name: string; provider: string; version: string; credential_template_id: number }) =>
    apiClient.post<{ success: boolean; message: string; module: ModuleLibrary }>('/api/registry/import-tfc', data).then((res) => res.data),

  checkModuleUpdates: (moduleId: number) =>
    apiClient.get<{ current_version: string; latest_version: string; update_available: boolean; versions?: Array<{ version: string; published_at?: string }> }>(
      `/api/registry/modules/${moduleId}/check-updates`
    ).then((res) => res.data),

  upgradeRegistryModule: (moduleId: number, targetVersion?: string) =>
    apiClient.post<{ success: boolean; message: string; module: ModuleLibrary }>(
      `/api/registry/modules/${moduleId}/upgrade`,
      null,
      { params: targetVersion ? { target_version: targetVersion } : {} }
    ).then((res) => res.data),

  // Variable Mappings
  getVariableMappings: (moduleId: number) =>
    apiClient.get<{ module_id: number; mappings: VariableMapping[] }>(
      `/api/project-modules/${moduleId}/variable-mappings`
    ).then((res) => res.data),

  createVariableMapping: (moduleId: number, data: VariableMappingRequest) =>
    apiClient.post<VariableMapping>(
      `/api/project-modules/${moduleId}/variable-mappings`,
      data
    ).then((res) => res.data),

  updateVariableMapping: (moduleId: number, mappingId: number, data: VariableMappingRequest) =>
    apiClient.put<VariableMapping>(
      `/api/project-modules/${moduleId}/variable-mappings/${mappingId}`,
      data
    ).then((res) => res.data),

  deleteVariableMapping: (moduleId: number, mappingId: number) =>
    apiClient.delete<{ success: boolean; message: string }>(
      `/api/project-modules/${moduleId}/variable-mappings/${mappingId}`
    ).then((res) => res.data),

  // Variable Mapping Templates
  getVariableMappingTemplates: () =>
    apiClient.get<{ templates: VariableMappingTemplate[] }>(
      '/api/project-modules/variable-mapping-templates'
    ).then((res) => res.data),

  createVariableMappingTemplate: (data: VariableMappingTemplateRequest) =>
    apiClient.post<VariableMappingTemplate>(
      '/api/project-modules/variable-mapping-templates',
      data
    ).then((res) => res.data),

  applyVariableMappingTemplate: (moduleId: number, templateId: number) =>
    apiClient.post<{ success: boolean; template_name: string; created_count: number; skipped_count: number; message: string }>(
      `/api/project-modules/${moduleId}/variable-mappings/apply-template/${templateId}`
    ).then((res) => res.data),
};
