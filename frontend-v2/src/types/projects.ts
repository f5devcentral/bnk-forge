// Project & Cloud Credential types

export interface ProjectDependency {
  project_id: number;
  outputs: string[];
}

export interface CloudCredentialTemplate {
  id: number;
  name: string;
  description?: string;
  provider: string; // aws, gcp, azure, ibm, ssh
  aws_auth_method?: string; // profile, access_keys, sso
  aws_profile?: string;
  region?: string;
  aws_access_key_id?: string;
  has_aws_secret_access_key: boolean;
  has_aws_session_token: boolean;
  aws_sso_enabled: boolean;
  aws_sso_start_url?: string;
  aws_sso_region?: string;
  aws_sso_account_id?: string;
  aws_sso_role_name?: string;
  aws_sso_authenticated_at?: string;
  aws_sso_token_expiry?: string;
  aws_credentials_expiry?: string;
  gcp_project_id?: string;
  has_gcp_credentials: boolean;
  azure_auth_method?: string; // service_principal, sso
  azure_subscription_id?: string;
  azure_tenant_id?: string;
  azure_client_id?: string;
  has_azure_client_secret?: boolean;
  has_azure_credentials: boolean;
  azure_sso_authenticated_at?: string;
  azure_sso_token_expiry?: string;
  has_ibmcloud_api_key: boolean;
  ibmcloud_resource_group?: string;
  ibm_cos_instance_name?: string;
  has_tfc_api_token?: boolean;
  tfc_hostname?: string;
  // SSH / On-Premises
  ssh_host?: string;
  ssh_port?: number;
  ssh_username?: string;
  ssh_auth_type?: string; // 'key' or 'password'
  has_ssh_password: boolean;
  has_ssh_key: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
  projects_count: number;
  // Passive cloud-API observation (RFC connectivity Phase 2). Populated by
  // every real boto3/ARM call site so the UI can show "AWS/Azure access OK as of X"
  // without re-clicking Test.
  last_successful_call_at?: string | null;
  last_error_at?: string | null;
  last_error_code?: string | null;
  last_error_message?: string | null;
}

export interface CloudRegionOption {
  value: string;
  label: string;
}

export interface IBCosInstanceOption {
  value: string;
  label: string;
  crn: string;
}

export interface SSODeviceCodeResponse {
  success: boolean;
  message: string;
  data: {
    user_code: string;
    verification_uri: string;
    verification_uri_complete: string;
    expires_in: number;
    interval: number;
    device_code: string;
  };
}

export interface SSOStatusResponse {
  success: boolean;
  sso_enabled: boolean;
  is_authenticated: boolean;
  authenticated_at?: string;
  token_expired: boolean;
  token_expiry?: string;
  credentials_expired: boolean;
  credentials_expiry?: string;
  has_credentials: boolean;
  can_refresh: boolean;
  needs_reauth: boolean;
}

// SSH Credential — first-class on-prem/bastion access (orthogonal to cloud provider)
export interface SSHCredential {
  id: number;
  name: string;
  description?: string;
  host: string;
  port: number;
  username: string;
  auth_type: string; // 'key' or 'password'
  has_password: boolean;
  has_private_key: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
  created_by?: string | null;
  clusters_count: number;
  projects_count: number;
  last_test_status?: 'ok' | 'failed' | 'running' | null;
  last_test_at?: string | null;
  last_test_message?: string | null;
}

export interface SSHCredentialCreate {
  name: string;
  description?: string;
  host: string;
  port?: number;
  username: string;
  auth_type: string; // 'key' or 'password'
  password?: string | null;
  private_key?: string | null;
  key_passphrase?: string | null;
  is_default?: boolean;
}

export interface CloudCredentialTemplateCreate {
  name: string;
  description?: string;
  provider: string;
  aws_auth_method?: string;
  aws_profile?: string;
  region?: string;
  aws_access_key_id?: string;
  aws_secret_access_key?: string;
  aws_session_token?: string;
  aws_sso_enabled?: boolean;
  aws_sso_start_url?: string;
  aws_sso_region?: string;
  aws_sso_account_id?: string;
  aws_sso_role_name?: string;
  gcp_credentials?: string;
  gcp_project_id?: string;
  azure_auth_method?: string;
  azure_subscription_id?: string;
  azure_tenant_id?: string;
  azure_client_id?: string;
  azure_client_secret?: string;
  azure_credentials?: string;
  ibmcloud_api_key?: string;
  ibmcloud_resource_group?: string;
  ibm_cos_instance_name?: string;
  // SSH / On-Premises
  ssh_host?: string;
  ssh_port?: number;
  ssh_username?: string;
  ssh_auth_type?: string;
  ssh_password?: string;
  ssh_private_key?: string;
  ssh_key_passphrase?: string;
  is_default?: boolean;
}

// Project type determines UI behavior and conditional sections
export type ProjectType = 'cloud-aws' | 'cloud-azure' | 'cloud-gcp' | 'cloud-ibm' | 'kubernetes' | 'bare-metal';

export interface Project {
  id: number;
  name: string;
  description?: string;
  project_type?: ProjectType;
  environment?: string;
  color: string;
  icon: string;
  is_active: boolean;
  has_deployments: boolean;
  module_count: number;
  deployed_count: number;
  failed_count: number;
  cluster_count?: number;
  project_variables?: Record<string, string>;
  dependent_projects?: ProjectDependency[];
  cloud_provider?: string;
  target_platform_profile?: import('./platform').PlatformProfile | null;
  platform_provider?: import('./platform').PlatformProvider | null;
  management_boundary?: import('./platform').ManagementBoundary | null;
  region?: string;
  credential_template_id?: number;
  credential_template?: CloudCredentialTemplate;
  ssh_credential_id?: number;
  ssh_credential?: SSHCredential;
  // MU-001: Ownership
  user_id?: number;
  owner_username?: string;
  // Backend configuration
  backend_type?: string; // "local" or "s3"
  state_storage_provider?: string;
  s3_state_bucket?: string;
  s3_state_key_prefix?: string;
  s3_state_region?: string;
  s3_use_native_locking?: boolean;
  s3_endpoint?: string;
  created_at: string;
  updated_at: string;
}

/**
 * Project create payload — matches backend ProjectCreate Pydantic schema.
 * S3 state config is nested in `state_config`, not flat fields.
 */
export interface ProjectCreateData {
  name: string;
  description?: string;
  project_type?: ProjectType;
  cloud_provider?: string;
  environment?: string;
  region?: string;
  backend_type?: string;
  state_config?: Record<string, unknown> | null;
  color?: string;
  icon?: string;
  credential_template_id?: number;
  ssh_credential_id?: number;
  target_platform_profile?: string;
}

/**
 * Project update payload — matches backend ProjectUpdate Pydantic schema.
 * All fields optional. SSO/cloud_credentials moved to credential templates.
 */
export interface ProjectUpdateData {
  name?: string;
  description?: string;
  project_type?: ProjectType;
  cloud_provider?: string;
  environment?: string;
  region?: string;
  backend_type?: string;
  state_config?: Record<string, unknown> | null;
  enabled?: boolean;
  credential_template_id?: number;
  ssh_credential_id?: number;
  color?: string;
  icon?: string;
}
