/**
 * QKView API — CWC diagnostic tarball collection for F5 BNK clusters.
 */
import { apiClient } from './client';
import type {
  QKViewListResponse,
  QKViewCreateRequest,
  QKViewCreateResponse,
  QKViewGetResponse,
  QKViewStatusResponse,
  QKViewCheckResponse,
  CWCAPISetupStatusResponse,
  CWCAPISetupResponse,
  QKViewDeleteResponse,
  QKViewCancelResponse,
  QKViewCleanupResponse,
} from '@/types';

export const qkviewApi = {
  /** Check if CWC QKView API is available on the cluster */
  checkAvailability: (clusterId: number) =>
    apiClient
      .get<QKViewCheckResponse>('/api/qkview/check', { params: { cluster_id: clusterId } })
      .then((res) => res.data),

  /** List all QKView jobs on the cluster */
  listQKViews: (clusterId: number) =>
    apiClient
      .get<QKViewListResponse>('/api/qkview/list', { params: { cluster_id: clusterId } })
      .then((res) => res.data),

  /** Create a new QKView diagnostic collection */
  createQKView: (data: QKViewCreateRequest) =>
    apiClient
      .post<QKViewCreateResponse>('/api/qkview/create', data)
      .then((res) => res.data),

  /** Get the status of a specific QKView */
  getQKViewStatus: (qkviewId: string, clusterId: number) =>
    apiClient
      .get<QKViewStatusResponse>(`/api/qkview/${qkviewId}/status`, {
        params: { cluster_id: clusterId },
      })
      .then((res) => res.data),

  /** Get details of a specific QKView */
  getQKView: (qkviewId: string, clusterId: number) =>
    apiClient
      .get<QKViewGetResponse>(`/api/qkview/${qkviewId}`, { params: { cluster_id: clusterId } })
      .then((res) => res.data),

  /** Download the QKView tarball */
  downloadQKView: (qkviewId: string, clusterId: number) =>
    apiClient
      .get(`/api/qkview/${qkviewId}/download`, {
        params: { cluster_id: clusterId },
        responseType: 'blob',
      })
      .then((res) => res.data),

  /** Delete a specific QKView */
  deleteQKView: (qkviewId: string, clusterId: number) =>
    apiClient
      .delete<QKViewDeleteResponse>(`/api/qkview/${qkviewId}`, { params: { cluster_id: clusterId } })
      .then((res) => res.data),

  /** Cancel a running QKView job */
  cancelQKView: (qkviewId: string, clusterId: number) =>
    apiClient
      .post<QKViewCancelResponse>(`/api/qkview/${qkviewId}/cancel`, null, {
        params: { cluster_id: clusterId },
      })
      .then((res) => res.data),

  /** Clean up legacy qkview client pods */
  cleanupPods: (clusterId: number) =>
    apiClient
      .post<QKViewCleanupResponse>('/api/qkview/cleanup-pods', null, {
        params: { cluster_id: clusterId },
      })
      .then((res) => res.data),

  /** Check if shared CWC API mTLS setup has been completed */
  getCWCAPISetupStatus: (clusterId: number) =>
    apiClient
      .get<CWCAPISetupStatusResponse>(`/api/licensing/${clusterId}/cwc-setup-status`)
      .then((res) => res.data),

  /** Run one-time cert-manager setup for shared CWC API mTLS */
  setupCWCAPI: (clusterId: number) =>
    apiClient
      .post<CWCAPISetupResponse>(`/api/licensing/${clusterId}/cwc-setup`)
      .then((res) => res.data),

  // Backward-compatible aliases during rename transition.
  getSetupStatus: (clusterId: number) => qkviewApi.getCWCAPISetupStatus(clusterId),
  runSetup: (clusterId: number) => qkviewApi.setupCWCAPI(clusterId),
};
