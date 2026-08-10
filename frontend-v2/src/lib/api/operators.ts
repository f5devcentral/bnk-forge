/**
 * Operators API methods
 *
 * Note: Registration token CRUD and generate-install-command methods were
 * removed in D3-CLEANUP. The kubeconfig-first fleet architecture made them obsolete.
 */
import { apiClient } from './client';
import type {
  ConnectedOperator,
  ConnectivityModeInfo,
  ConnectivitySetup,
  OperatorConnectivityMode,
} from '@/types';

export const operatorsApi = {
  // Connected Operators
  listOperators: (connectedOnly: boolean = false) =>
    apiClient.get<ConnectedOperator[]>('/api/operators', { params: { connected_only: connectedOnly } }).then((res) => res.data),

  getOperator: (operatorId: string) =>
    apiClient.get<ConnectedOperator>(`/api/operators/${operatorId}`).then((res) => res.data),

  deleteOperator: (operatorId: string) =>
    apiClient.delete<{ success: boolean; message: string }>(`/api/operators/${operatorId}`).then((res) => res.data),

  sendOperatorCommand: (operatorId: string, action: string, payload?: Record<string, unknown>, timeout?: number) =>
    apiClient.post<{ success: boolean; command_id: string; result: Record<string, unknown> }>(
      `/api/operators/${operatorId}/command`, { action, payload, timeout }
    ).then((res) => res.data),

  linkOperatorToCluster: (operatorId: string, clusterId: number) =>
    apiClient.post<{ success: boolean; message: string; operator: ConnectedOperator }>(
      `/api/operators/${operatorId}/link-cluster`, { cluster_id: clusterId }
    ).then((res) => res.data),

  unlinkOperatorFromCluster: (operatorId: string) =>
    apiClient.post<{ success: boolean; message: string; operator: ConnectedOperator }>(
      `/api/operators/${operatorId}/unlink-cluster`
    ).then((res) => res.data),

  // Connectivity Modes
  getConnectivityModes: () =>
    apiClient.get<ConnectivityModeInfo[]>('/api/operators/connectivity-modes').then((res) => res.data),

  setOperatorConnectivity: (operatorId: string, mode: OperatorConnectivityMode, config?: Record<string, unknown>) =>
    apiClient.post<{ operator: ConnectedOperator; setup: ConnectivitySetup }>(
      `/api/operators/${operatorId}/connectivity`, { mode, config }
    ).then((res) => res.data),

  previewConnectivitySetup: (operatorId: string, mode: OperatorConnectivityMode, config?: Record<string, unknown>) =>
    apiClient.post<ConnectivitySetup>(
      `/api/operators/${operatorId}/connectivity/preview`, { mode, config }
    ).then((res) => res.data),

  openReverseTunnel: (operatorId: string) =>
    apiClient.post<{ success: boolean; ssh_host: string; remote_port: number; local_port: number; message: string }>(
      `/api/operators/${operatorId}/reverse-tunnel/open`
    ).then((res) => res.data),
};
