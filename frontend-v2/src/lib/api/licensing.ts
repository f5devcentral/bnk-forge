/**
 * Licensing API — CWC license management via connected operator pod.
 *
 * K8S-UX-010: All licensing operations are dispatched through the operator
 * pod, which makes mTLS + Bearer token HTTPS calls to the CWC REST API.
 */
import { apiClient } from './client';
import type {
  LicenseStatusResponse,
  LicenseReportResponse,
  LicenseActionResponse,
  LicenseActivateRequest,
  LicenseRenewRequest,
  LicenseReceiptRequest,
  CWCStatusResponse,
  JwksValidationResponse,
} from '@/types';

export const licensingApi = {
  /** Get CWC license and telemetry status for a cluster */
  getStatus: (clusterId: number) =>
    apiClient
      .get<LicenseStatusResponse>(`/api/licensing/${clusterId}/status`)
      .then((res) => res.data),

  /** Get CWC telemetry report for a cluster */
  getReport: (clusterId: number) =>
    apiClient
      .get<LicenseReportResponse>(`/api/licensing/${clusterId}/report`)
      .then((res) => res.data),

  /** Reactivate/switch CWC license on a cluster (first-time activation) */
  activate: (clusterId: number, data: LicenseActivateRequest) =>
    apiClient
      .post<LicenseActionResponse>(`/api/licensing/${clusterId}/activate`, data)
      .then((res) => res.data),

  /** Renew license — full cycle: CPCL cleanup → CWC restart → activate */
  renew: (clusterId: number, data: LicenseRenewRequest) =>
    apiClient
      .post<LicenseActionResponse>(`/api/licensing/${clusterId}/renew`, data)
      .then((res) => res.data),

  /** Send signed manifest (receipt) back to CWC */
  postReceipt: (clusterId: number, data: LicenseReceiptRequest) =>
    apiClient
      .post<LicenseActionResponse>(`/api/licensing/${clusterId}/receipt`, data)
      .then((res) => res.data),

  /** Validate and fix JWKS ConfigMap (cpcl-key-cm) on a cluster */
  validateJwks: (clusterId: number) =>
    apiClient
      .post<JwksValidationResponse>(`/api/licensing/${clusterId}/validate-jwks`)
      .then((res) => res.data),

  /** Full CWC connectivity and certificate status check */
  getCWCStatus: (clusterId: number) =>
    apiClient
      .get<CWCStatusResponse>(`/api/licensing/${clusterId}/cwc-status`)
      .then((res) => res.data),
};
