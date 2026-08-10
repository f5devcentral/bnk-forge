// QKView types — CWC diagnostic tarball collection for F5 BNK clusters

export interface QKViewCheckResponse {
  available: boolean;
  message: string;
  operator_dispatch?: boolean;
}

export interface QKViewCreateRequest {
  cluster_id: number;
  namespace?: string;
  timeout?: string;
  filename?: string;
  pod_patterns?: string[];
  include_core_files?: boolean;
  core_files_max?: number;
  core_files_since?: string;
}

export interface QKViewCreateResponse {
  id?: string;
  filename?: string;
  operator_dispatch?: boolean;
  raw?: string;
  [key: string]: unknown;
}

export interface QKViewStatusNested {
  status: string;
  msgs?: string[];
  nested?: Record<string, QKViewStatusNested>;
}

export interface QKViewStatusResponse {
  status: string;
  progress?: number;
  operator_dispatch?: boolean;
  nested?: Record<string, QKViewStatusNested>;
}

export interface QKViewGetResponse {
  id?: string;
  status?: string;
  filename?: string;
  operator_dispatch?: boolean;
  [key: string]: unknown;
}

export interface QKViewItem {
  id?: string;
  filename?: string;
  namespace?: string;
  timeout?: string;
  pod_patterns?: string[];
  status?: string;
}

export interface QKViewListResponse {
  qkviews: QKViewItem[];
  error?: string;
  operator_dispatch?: boolean;
}

// Shared CWC API mTLS setup types

export interface CWCAPISetupStatusResponse {
  setup_complete: boolean;
  cert_manager_available: boolean;
  server_cert_exists: boolean;
  client_cert_exists: boolean;
  certs_mounted?: boolean;
  operator_dispatch?: boolean;
  message: string;
}

export interface CWCAPISetupStep {
  step: string;
  status: 'ok' | 'skipped';
  detail?: string;
}

export interface CWCAPISetupResponse {
  success: boolean;
  message: string;
  steps: CWCAPISetupStep[];
  operator_dispatch?: boolean;
}

// Backward-compatible aliases during rename transition.
export type QKViewSetupStatusResponse = CWCAPISetupStatusResponse;
export type QKViewSetupStep = CWCAPISetupStep;
export type QKViewSetupResponse = CWCAPISetupResponse;

export interface QKViewDeleteResponse {
  deleted?: boolean;
  success?: boolean;
  operator_dispatch?: boolean;
  [key: string]: unknown;
}

export interface QKViewCancelResponse {
  cancelled?: boolean;
  success?: boolean;
  operator_dispatch?: boolean;
  [key: string]: unknown;
}

export interface QKViewCleanupResponse {
  cleaned: boolean;
  operator_dispatch?: boolean;
  [key: string]: unknown;
}
