/**
 * Bare-metal DPU deployment API client.
 */
import { apiClient } from './client';
import type {
  BareMetalHost,
  BareMetalHostCreate,
  BareMetalHostUpdate,
  BareMetalDeployment,
  BareMetalDeploymentCreate,
  BareMetalDeploymentResumeRequest,
  BareMetalDiscoveryRequest,
  BareMetalDiscoveryResponse,
  DiscoveryTriggerResponse,
  DeployableRelease,
  DeploymentPlanPreview,
} from '@/types/bare-metal';

// --- Hosts ---

export const bareMetalHostsApi = {
  listHosts: async (projectId: number): Promise<BareMetalHost[]> => {
    const resp = await apiClient.get<{ hosts: BareMetalHost[] }>(
      `/api/projects/${projectId}/bare-metal/hosts`
    );
    return resp.data.hosts;
  },

  getHost: async (projectId: number, hostId: number): Promise<BareMetalHost> => {
    const resp = await apiClient.get<BareMetalHost>(
      `/api/projects/${projectId}/bare-metal/hosts/${hostId}`
    );
    return resp.data;
  },

  createHost: async (projectId: number, data: BareMetalHostCreate): Promise<BareMetalHost> => {
    const resp = await apiClient.post<BareMetalHost>(
      `/api/projects/${projectId}/bare-metal/hosts`,
      data
    );
    return resp.data;
  },

  updateHost: async (
    projectId: number,
    hostId: number,
    data: BareMetalHostUpdate
  ): Promise<BareMetalHost> => {
    const resp = await apiClient.put<BareMetalHost>(
      `/api/projects/${projectId}/bare-metal/hosts/${hostId}`,
      data
    );
    return resp.data;
  },

  deleteHost: async (projectId: number, hostId: number): Promise<void> => {
    await apiClient.delete(`/api/projects/${projectId}/bare-metal/hosts/${hostId}`);
  },

  triggerDiscovery: async (
    projectId: number,
    hostId: number,
    data: BareMetalDiscoveryRequest
  ): Promise<DiscoveryTriggerResponse> => {
    const resp = await apiClient.post<DiscoveryTriggerResponse>(
      `/api/projects/${projectId}/bare-metal/hosts/${hostId}/discover`,
      data
    );
    return resp.data;
  },

  getDiscoveryResult: async (
    projectId: number,
    hostId: number
  ): Promise<BareMetalDiscoveryResponse | null> => {
    const resp = await apiClient.get<BareMetalDiscoveryResponse>(
      `/api/projects/${projectId}/bare-metal/hosts/${hostId}/discovery-result`
    );
    return resp.data;
  },
};

// --- Deployments ---

export const bareMetalDeploymentsApi = {
  listDeployments: async (
    projectId: number,
    hostId?: number
  ): Promise<BareMetalDeployment[]> => {
    const params = hostId ? { host_id: hostId } : {};
    const resp = await apiClient.get<{ deployments: BareMetalDeployment[] }>(
      `/api/projects/${projectId}/bare-metal/deployments`,
      { params }
    );
    return resp.data.deployments;
  },

  getDeployment: async (
    projectId: number,
    deploymentId: number
  ): Promise<BareMetalDeployment> => {
    const resp = await apiClient.get<BareMetalDeployment>(
      `/api/projects/${projectId}/bare-metal/deployments/${deploymentId}`
    );
    return resp.data;
  },

  previewDeployment: async (
    projectId: number,
    data: BareMetalDeploymentCreate
  ): Promise<DeploymentPlanPreview> => {
    const resp = await apiClient.post<DeploymentPlanPreview>(
      `/api/projects/${projectId}/bare-metal/deployments/preview`,
      data
    );
    return resp.data;
  },

  createDeployment: async (
    projectId: number,
    data: BareMetalDeploymentCreate
  ): Promise<BareMetalDeployment> => {
    const resp = await apiClient.post<BareMetalDeployment>(
      `/api/projects/${projectId}/bare-metal/deployments`,
      data
    );
    return resp.data;
  },

  resumeDeployment: async (
    projectId: number,
    deploymentId: number,
    data: BareMetalDeploymentResumeRequest
  ): Promise<BareMetalDeployment> => {
    const resp = await apiClient.post<BareMetalDeployment>(
      `/api/projects/${projectId}/bare-metal/deployments/${deploymentId}/resume`,
      data
    );
    return resp.data;
  },

  cancelDeployment: async (
    projectId: number,
    deploymentId: number
  ): Promise<BareMetalDeployment> => {
    const resp = await apiClient.post<BareMetalDeployment>(
      `/api/projects/${projectId}/bare-metal/deployments/${deploymentId}/cancel`
    );
    return resp.data;
  },

  getStepLogs: async (
    projectId: number,
    deploymentId: number,
    stepIndex: number
  ): Promise<{ output_log: string | null; error_message: string | null; exit_code: number | null }> => {
    const resp = await apiClient.get<{
      output_log: string | null;
      error_message: string | null;
      exit_code: number | null;
    }>(
      `/api/projects/${projectId}/bare-metal/deployments/${deploymentId}/steps/${stepIndex}/logs`
    );
    return resp.data;
  },
};

// --- Deployable Releases ---

export const deployableReleasesApi = {
  listReleases: async (): Promise<DeployableRelease[]> => {
    const resp = await apiClient.get<{ releases: DeployableRelease[] }>(
      '/api/bare-metal/deployable-releases'
    );
    return resp.data.releases;
  },

  getRelease: async (releaseId: number): Promise<DeployableRelease> => {
    const resp = await apiClient.get<DeployableRelease>(
      `/api/bare-metal/deployable-releases/${releaseId}`
    );
    return resp.data;
  },

  activate: async (releaseId: number, isActive: boolean): Promise<DeployableRelease> => {
    const resp = await apiClient.post<DeployableRelease>(
      `/api/bare-metal/deployable-releases/${releaseId}/activate`,
      { is_active: isActive }
    );
    return resp.data;
  },

  setDefault: async (releaseId: number): Promise<DeployableRelease> => {
    const resp = await apiClient.post<DeployableRelease>(
      `/api/bare-metal/deployable-releases/${releaseId}/set-default`
    );
    return resp.data;
  },
};
