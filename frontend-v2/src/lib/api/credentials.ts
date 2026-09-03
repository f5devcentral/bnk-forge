/**
 * Credentials & Cloud Authentication API methods
 */
import { apiClient } from './client';
import type {
  CloudCredentialTemplate,
  CloudCredentialTemplateCreate,
  CloudRegionOption,
  IBCosInstanceOption,
  SSODeviceCodeResponse,
  SSOStatusResponse,
} from '@/types';

export const credentialsApi = {
  // Cloud Credential Templates
  listCredentialTemplates: (provider?: string) =>
    apiClient.get<CloudCredentialTemplate[]>('/api/credential-templates', { params: { provider } }).then((res) => res.data),

  getCredentialTemplate: (id: number) =>
    apiClient.get<CloudCredentialTemplate>(`/api/credential-templates/${id}`).then((res) => res.data),

  createCredentialTemplate: (data: CloudCredentialTemplateCreate) =>
    apiClient.post<CloudCredentialTemplate>('/api/credential-templates', data).then((res) => res.data),

  updateCredentialTemplate: (id: number, data: Partial<CloudCredentialTemplateCreate>) =>
    apiClient.put<CloudCredentialTemplate>(`/api/credential-templates/${id}`, data).then((res) => res.data),

  deleteCredentialTemplate: (id: number) =>
    apiClient.delete(`/api/credential-templates/${id}`).then((res) => res.data),

  testCredentialTemplate: (id: number) =>
    apiClient.post<{ success: boolean; message?: string; error?: string; arn?: string; account_id?: string; hostname?: string; ssh_banner?: string; details?: string }>(`/api/credential-templates/${id}/test`).then((res) => res.data),

  // SSO Authentication for templates
  authenticateTemplateSSO: (id: number) =>
    apiClient.post<SSODeviceCodeResponse>(`/api/credential-templates/${id}/authenticate-sso`).then((res) => res.data),

  pollTemplateSSO: (id: number, deviceCode: string) =>
    apiClient.post(`/api/credential-templates/${id}/poll-sso`, { device_code: deviceCode }).then((res) => res.data),

  refreshTemplateSSO: (id: number) =>
    apiClient.post(`/api/credential-templates/${id}/refresh-sso`).then((res) => res.data),

  getTemplateSSOStatus: (id: number) =>
    apiClient.get<SSOStatusResponse>(`/api/credential-templates/${id}/sso-status`).then((res) => res.data),

  queryCloudRegions: (data: { provider: string; ibmcloud_api_key?: string }) =>
    apiClient.post<{ provider: string; regions: CloudRegionOption[] }>('/api/cloud-auth/regions/query', data).then((res) => res.data),

  listIBMRegions: (templateId?: number) =>
    apiClient.get<{ provider: string; regions: CloudRegionOption[] }>('/api/cloud-auth/ibm/regions', { params: { template_id: templateId } }).then((res) => res.data),

  listAWSRegions: () =>
    apiClient.get<{ provider: string; regions: CloudRegionOption[] }>('/api/cloud-auth/aws/regions').then((res) => res.data),

  listAzureRegions: () =>
    apiClient.get<{ provider: string; regions: CloudRegionOption[] }>('/api/cloud-auth/azure/regions').then((res) => res.data),

  listIBMCosInstances: (ibmcloudApiKey: string) =>
    apiClient.post<{ instances: IBCosInstanceOption[] }>('/api/cloud-auth/ibm/cos-instances/query', { ibmcloud_api_key: ibmcloudApiKey }).then((res) => res.data),

  // Azure Cloud Authentication
  initiateAzureSSO: (data: { tenant_id?: string; client_id?: string; template_id?: number }) =>
    apiClient.post<SSODeviceCodeResponse>('/api/cloud-auth/azure/sso/initiate', data).then((res) => res.data),

  pollAzureSSO: (data: { device_code: string; tenant_id?: string; client_id?: string; template_id?: number }) =>
    apiClient.post<{
      success: boolean;
      message?: string;
      pending?: boolean;
      data?: {
        access_token: string;
        expires_in: number;
        refresh_token?: string;
      };
    }>('/api/cloud-auth/azure/sso/poll', data).then((res) => res.data),

  listAzureSubscriptions: (data: { access_token: string }) =>
    apiClient.post<{
      success: boolean;
      subscriptions: Array<{ subscription_id: string; display_name: string; state: string }>;
    }>('/api/cloud-auth/azure/subscriptions', data).then((res) => res.data),

  // Cloud Authentication
  initiateAWSSSO: (data: { start_url: string; region?: string; project_id?: number }) =>
    apiClient.post<{
      success: boolean;
      message: string;
      data: {
        user_code: string;
        verification_uri: string;
        verification_uri_complete: string;
        expires_in: number;
        interval: number;
        client_id: string;
        client_secret: string;
        device_code: string;
        region: string;
      };
    }>('/api/cloud-auth/aws/sso/initiate', data).then((res) => res.data),

  pollAWSSSO: (data: { client_id: string; client_secret: string; device_code: string; region?: string; project_id?: number }) =>
    apiClient.post<{
      success: boolean;
      message: string;
      pending?: boolean;
      data?: {
        access_token: string;
        expires_at: string;
        has_refresh_token: boolean;
      };
    }>('/api/cloud-auth/aws/sso/poll', data).then((res) => res.data),

  listAWSAccounts: (data: { access_token: string; region?: string }) =>
    apiClient.post<{
      success: boolean;
      accounts: Array<{ account_id: string; account_name: string; email_address: string }>;
      count: number;
    }>('/api/cloud-auth/aws/accounts', data).then((res) => res.data),

  listAWSAccountRoles: (data: { access_token: string; account_id: string; region?: string }) =>
    apiClient.post<{
      success: boolean;
      roles: Array<{ role_name: string; account_id: string }>;
      count: number;
    }>('/api/cloud-auth/aws/accounts/roles', data).then((res) => res.data),

  getAWSCredentials: (data: { access_token: string; account_id: string; role_name: string; region?: string; project_id: number }) =>
    apiClient.post<{
      success: boolean;
      message: string;
      data: {
        account_id: string;
        role_name: string;
        expiration: string;
      };
    }>('/api/cloud-auth/aws/credentials', data).then((res) => res.data),

  getAWSCredentialStatus: (projectId: number) =>
    apiClient.get<{
      success: boolean;
      configured: boolean;
      message?: string;
      data?: {
        auth_method?: string;
        account_id?: string;
        role_name?: string;
        role_arn?: string;
        region?: string;
        expiration?: string;
        has_session_token?: boolean;
      };
    }>(`/api/cloud-auth/aws/credentials/${projectId}`).then((res) => res.data),
};
