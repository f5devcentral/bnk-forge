/**
 * Stacks API methods
 */
import { apiClient } from './client';
import type {
  ImportedBlueprintProjectCreateResponse,
  StackTemplate,
  StackTemplateList,
  StackPreview,
  StackInstance,
  StackInstanceCreate,
  StackStatus,
  StackRequiredInputs,
  StackPrerequisitesCheck,
  SaveAsTemplateRequest,
  DuplicateTemplateRequest,
} from '@/types';

export const stacksApi = {
  // Stacks (P2-4: Deployment Stacks)
  fetchStackTemplates: (params?: { category?: string; cloud_provider?: string; tags?: string; is_featured?: boolean }) =>
    apiClient.get<StackTemplateList[]>('/api/stacks/templates', { params }).then((res) => res.data),

  fetchStackTemplate: (slug: string) =>
    slug.startsWith('release-')
      ? apiClient.get<StackTemplate>(`/api/stacks/releases/${slug.replace('release-', '')}`).then((res) => res.data)
      : apiClient.get<StackTemplate>(`/api/stacks/templates/${slug}`).then((res) => res.data),

  previewStackTemplate: (slug: string) =>
    slug.startsWith('release-')
      ? apiClient.get<StackPreview>(`/api/stacks/releases/${slug.replace('release-', '')}/preview`).then((res) => res.data)
      : apiClient.get<StackPreview>(`/api/stacks/templates/${slug}/preview`).then((res) => res.data),

  getStackRequiredInputs: (slug: string, projectId?: number, bareMetalHostId?: string) => {
    const params = {
      ...(projectId ? { project_id: projectId } : {}),
      ...(bareMetalHostId ? { bare_metal_host_id: bareMetalHostId } : {}),
    };
    if (slug.startsWith('release-')) {
      return apiClient
        .get<StackRequiredInputs>(`/api/stacks/releases/${slug.replace('release-', '')}/required-inputs`, { params })
        .then((res) => res.data);
    }
    return apiClient
      .get<StackRequiredInputs>(`/api/stacks/templates/${slug}/required-inputs`, { params })
      .then((res) => res.data);
  },

  /** S19-001: Check if project satisfies stack template prerequisites (secrets) */
  checkStackPrerequisites: (slug: string, projectId: number) =>
    apiClient.get<StackPrerequisitesCheck>(`/api/stacks/templates/${slug}/check-prerequisites`, { params: { project_id: projectId } }).then((res) => res.data),

  createStackInstance: (projectId: number, data: StackInstanceCreate) =>
    apiClient.post<StackInstance>(`/api/stacks/projects/${projectId}/stacks`, data).then((res) => res.data),

  fetchStackInstances: (projectId: number, status?: string) =>
    apiClient.get<StackInstance[]>(`/api/stacks/projects/${projectId}/stacks`, { params: { status } }).then((res) => res.data),

  fetchStackInstance: (projectId: number, stackId: number) =>
    apiClient.get<StackInstance>(`/api/stacks/projects/${projectId}/stacks/${stackId}`).then((res) => res.data),

  deployStack: (projectId: number, stackId: number) =>
    apiClient.post<{ status: string; message: string; current_step: number; total_steps: number; deployed_modules: number[] }>(
      `/api/stacks/projects/${projectId}/stacks/${stackId}/deploy`
    ).then((res) => res.data),

  // Run deployment (init + apply) on stack modules
  runStackDeployment: (projectId: number, stackId: number) =>
    apiClient.post<{ status: string; message: string; queued_modules: number; total_modules: number }>(
      `/api/stacks/projects/${projectId}/stacks/${stackId}/run-deploy`
    ).then((res) => res.data),

  fetchStackStatus: (projectId: number, stackId: number) =>
    apiClient.get<StackStatus>(`/api/stacks/projects/${projectId}/stacks/${stackId}/status`).then((res) => res.data),

  destroyStack: (projectId: number, stackId: number, force?: boolean) =>
    apiClient.delete<{ status: string; message: string }>(
      `/api/stacks/projects/${projectId}/stacks/${stackId}`,
      { params: force ? { force: true } : undefined }
    ).then((res) => res.data),

  // Phase 4: Custom stacks & marketplace
  saveProjectAsTemplate: (projectId: number, data: SaveAsTemplateRequest) =>
    apiClient.post<StackTemplate>(`/api/stacks/projects/${projectId}/save-as-template`, data).then((res) => res.data),

  duplicateTemplate: (templateId: number, data: DuplicateTemplateRequest) =>
    apiClient.post<StackTemplate>(`/api/stacks/templates/${templateId}/duplicate`, data).then((res) => res.data),

  createProjectFromImportedBlueprint: (releaseId: number, data: {
    name: string;
    description?: string;
    project_type?: string;
    cloud_provider?: string;
    environment?: string;
    region?: string;
    credential_template_id?: number;
    backend_type?: string;
    color?: string;
    icon?: string;
    variables?: Record<string, unknown>;
  }) => apiClient.post<ImportedBlueprintProjectCreateResponse>(`/api/stacks/releases/${releaseId}/projects`, data).then((res) => res.data),

  addImportedBlueprintToProject: (projectId: number, releaseId: number, data: {
    variables?: Record<string, unknown>;
  }) => apiClient.post<ImportedBlueprintProjectCreateResponse>(`/api/stacks/projects/${projectId}/releases/${releaseId}`, data).then((res) => res.data),
};
