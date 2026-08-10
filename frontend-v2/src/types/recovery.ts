/**
 * Types for BNK platform recovery operations.
 */

export interface RecoveryStep {
  step: string;
  status: 'ok' | 'failed' | 'skipped' | 'warning';
  detail?: string;
  error?: string;
}

export interface RecoveryStatusResponse {
  cwc_cert_stale: boolean;
  /** 'not_applicable' = CWC certs are managed outside Forge (direct-helm install) */
  cwc_cert_status: 'ok' | 'stale' | 'not_applicable' | 'unknown';
  vlans_failed: boolean;
  cwc_cert_detail: string | null;
  vlans_detail: string | null;
  platform_healthy: boolean;
}

export interface CWCCertResyncResponse {
  success: boolean;
  message: string;
  steps: RecoveryStep[];
}

export interface PlatformRestartRequest {
  restart_controller: boolean;
  restart_flo: boolean;
  restart_tmm: boolean;
}

export interface PlatformRestartResult {
  component: string;
  status: 'restarted' | 'not_found';
  deleted_pods?: string[];
  message: string;
}

export interface PlatformRestartResponse {
  success: boolean;
  message: string;
  restarted: PlatformRestartResult[];
}
