import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  DpfDetectResponse,
  DpfHealthResponse,
  DpfDpuDevice,
  DpfDpuCluster,
  DpfDpu,
  DpfDpuSet,
  DpfBfb,
  DpfDpuFlavor,
  DpfDpuService,
  DpfDpuDeployment,
  DpfDpuServiceChain,
  DpfDpuServiceInterface,
  DpfServiceChain,
  DpfServiceInterface,
} from '@/types';
import { POLL_INTERVALS } from '@/lib/constants';
import { queryKeys } from '@/lib/queryKeys';

// ========================================================================
// DPF Detection (lightweight)
//
// Checks if DPF is installed by fetching only 3 CRD types.
// Use this to conditionally show/hide the DPF panel in the sidebar.
// ========================================================================

export function useDpfDetect(
  clusterId: number,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.dpfDetect(clusterId),
    queryFn: () => api.getDpfDetect(clusterId),
    enabled: options?.enabled !== false && !!clusterId,
    staleTime: 60_000, // DPF installation status rarely changes
    placeholderData: (previousData) => previousData,
  });
}

// ========================================================================
// DPF Unified Data Hook
//
// Single fetch for all DPF resource types. Returns health analysis and
// full resource inventory. All DPF views share this cache key, so
// switching between DPF tabs is instant (no re-fetch).
// ========================================================================

export function useDpfData(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.dpfData(clusterId),
    queryFn: () => api.getDpfData(clusterId),
    enabled: options?.enabled !== false && !!clusterId,
    refetchInterval: options?.pollingEnabled !== false ? POLL_INTERVALS.SLOW : false,
    placeholderData: (previousData) => previousData,
  });
}

// Convenience selector — returns just the health slice
export function useDpfHealth(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: query.data?.health as DpfHealthResponse | undefined,
  };
}

// Convenience selector — returns just the detect-like summary from full data
export function useDpfSummary(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  const health = query.data?.health;
  return {
    ...query,
    data: health
      ? ({
          detected: health.operator.configured,
          version: health.operator.version,
          operator: {
            configured: health.operator.configured,
            ready: health.operator.ready,
          },
          devices: { total: health.devices.total },
          dpuclusters: health.clusters.total,
          cluster_id: query.data!.cluster_id,
        } satisfies DpfDetectResponse)
      : undefined,
  };
}

// ========================================================================
// Typed resource selectors — extract raw K8s objects from unified data
// ========================================================================

/** Extract DPUDevice resources from the unified data response. */
export function useDpfDevices(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.dpudevice ?? []) as DpfDpuDevice[],
  };
}

/** Extract DPUCluster resources from the unified data response. */
export function useDpfClusters(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.dpucluster ?? []) as DpfDpuCluster[],
  };
}

/** Extract DPU lifecycle objects from the unified data response. */
export function useDpfDpus(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.dpu ?? []) as DpfDpu[],
  };
}

/** Extract DPUSet resources from the unified data response. */
export function useDpfSets(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.dpuset ?? []) as DpfDpuSet[],
  };
}

/** Extract BFB resources from the unified data response. */
export function useDpfBfbs(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.bfb ?? []) as DpfBfb[],
  };
}

/** Extract DPUFlavor resources from the unified data response. */
export function useDpfFlavors(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.dpuflavor ?? []) as DpfDpuFlavor[],
  };
}

// =============================================================================
// Service resource selectors (svc.dpu.nvidia.com)
// =============================================================================

/** Extract DPUService resources from the unified data response. */
export function useDpfServices(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.dpuservice ?? []) as DpfDpuService[],
  };
}

/** Extract DPUDeployment resources from the unified data response. */
export function useDpfDeployments(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.dpudeployment ?? []) as DpfDpuDeployment[],
  };
}

/** Extract DPUServiceChain resources from the unified data response. */
export function useDpfServiceChains(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.dpuservicechain ?? []) as DpfDpuServiceChain[],
  };
}

/** Extract DPUServiceInterface resources from the unified data response. */
export function useDpfServiceInterfaces(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.dpuserviceinterface ?? []) as DpfDpuServiceInterface[],
  };
}

/** Extract per-DPU ServiceChain resources from the unified data response. */
export function useDpfRealizedServiceChains(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.servicechain ?? []) as DpfServiceChain[],
  };
}

/** Extract per-DPU ServiceInterface resources from the unified data response. */
export function useDpfRealizedServiceInterfaces(
  clusterId: number,
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useDpfData(clusterId, options);
  return {
    ...query,
    data: (query.data?.resources?.serviceinterface ?? []) as DpfServiceInterface[],
  };
}
