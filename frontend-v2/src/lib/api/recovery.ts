/**
 * BNK Platform Recovery API — post-reboot recovery operations.
 *
 * Two recovery workflows:
 * 1. CWC Cert Re-sync — fixes mTLS cert mismatch after cluster reboot
 * 2. Platform Restart — fixes gRPC sync issues (VLAN Programmed=False)
 */
import { apiClient } from './client';
import type {
  RecoveryStatusResponse,
  CWCCertResyncResponse,
  PlatformRestartRequest,
  PlatformRestartResponse,
} from '@/types';

const BASE = '/api/k8s/clusters';

export const recoveryApi = {
  /** Check what (if anything) needs recovery */
  getStatus: (clusterId: number) =>
    apiClient
      .get<RecoveryStatusResponse>(`${BASE}/${clusterId}/recovery/status`)
      .then((res) => res.data),

  /** Re-sync CWC certs from cert-manager and restart CWC + agent pods */
  resyncCWCCerts: (clusterId: number) =>
    apiClient
      .post<CWCCertResyncResponse>(`${BASE}/${clusterId}/recovery/cwc-certs`)
      .then((res) => res.data),

  /** Restart platform components to fix gRPC sync issues */
  platformRestart: (clusterId: number, data: PlatformRestartRequest) =>
    apiClient
      .post<PlatformRestartResponse>(`${BASE}/${clusterId}/recovery/platform-restart`, data)
      .then((res) => res.data),
};
