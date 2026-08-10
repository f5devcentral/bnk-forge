/**
 * TMM Debug Sidecar API — exec diagnostic commands into TMM debug containers.
 *
 * Endpoints exec directly into the `debug` sidecar container of f5-tmm-* pods
 * via kubeconfig. Does NOT use the bnk-forge-agent pod.
 */
import { apiClient } from './client';
import type {
  TMMDebugPodsResponse,
  TMMDebugExecRequest,
  TMMDebugExecResponse,
  TMMDebugTmctlRequest,
  TMMDebugTmctlResponse,
  TMMDebugConfigviewRequest,
  TMMDebugConfigviewUuidsRequest,
  TMMDebugConfigviewUuidsResponse,
  TMMDebugBdtRequest,
} from '@/types';

const BASE = '/api/k8s/clusters';

export const tmmDebugApi = {
  /** List TMM pods with debug sidecar availability */
  listPods: (clusterId: number) =>
    apiClient
      .get<TMMDebugPodsResponse>(`${BASE}/${clusterId}/tmm-debug/pods`)
      .then((res) => res.data),

  /** Execute a raw command in the debug sidecar */
  exec: (clusterId: number, data: TMMDebugExecRequest) =>
    apiClient
      .post<TMMDebugExecResponse>(`${BASE}/${clusterId}/tmm-debug/exec`, data)
      .then((res) => res.data),

  /** Execute a structured tmctl query with parsed tabular output */
  tmctl: (clusterId: number, data: TMMDebugTmctlRequest) =>
    apiClient
      .post<TMMDebugTmctlResponse>(`${BASE}/${clusterId}/tmm-debug/tmctl`, data)
      .then((res) => res.data),

  /** Execute configview uuid to inspect a specific CR config */
  configview: (clusterId: number, data: TMMDebugConfigviewRequest) =>
    apiClient
      .post<TMMDebugExecResponse>(`${BASE}/${clusterId}/tmm-debug/configview`, data)
      .then((res) => res.data),

  /** Discover available configview UUIDs */
  configviewUuids: (clusterId: number, data: TMMDebugConfigviewUuidsRequest) =>
    apiClient
      .post<TMMDebugConfigviewUuidsResponse>(`${BASE}/${clusterId}/tmm-debug/configview/uuids`, data)
      .then((res) => res.data),

  /** Execute bdt_cli networking diagnostic */
  bdt: (clusterId: number, data: TMMDebugBdtRequest) =>
    apiClient
      .post<TMMDebugExecResponse>(`${BASE}/${clusterId}/tmm-debug/bdt`, data)
      .then((res) => res.data),
};
