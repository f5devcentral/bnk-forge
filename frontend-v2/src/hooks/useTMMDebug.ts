/**
 * React Query hooks for TMM Debug Sidecar operations.
 *
 * Provides hooks for:
 *   - Listing TMM pods with debug sidecar availability
 *   - Executing tmctl, configview, bdt_cli, and raw commands
 *
 * All mutations are one-shot commands (not streaming).
 */
import { useQuery } from '@tanstack/react-query';
import { tmmDebugApi } from '@/lib/api/tmm-debug';

import type {
  TMMDebugExecRequest,
  TMMDebugTmctlRequest,
  TMMDebugConfigviewRequest,
  TMMDebugConfigviewUuidsRequest,
  TMMDebugBdtRequest,
} from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ---------------------------------------------------------------------------
// Query Keys
// ---------------------------------------------------------------------------

export const TMM_DEBUG_KEYS = {
  all: ['tmm-debug'] as const,
  pods: (clusterId: number) => [...TMM_DEBUG_KEYS.all, 'pods', clusterId] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** List TMM pods with debug sidecar availability */
export function useTMMDebugPods(clusterId: number, enabled = true) {
  return useQuery({
    queryKey: TMM_DEBUG_KEYS.pods(clusterId),
    queryFn: () => tmmDebugApi.listPods(clusterId),
    enabled: enabled && clusterId > 0,
    staleTime: 30_000, // 30s — pod list doesn't change often
    retry: 1,
  });
}

// ---------------------------------------------------------------------------
// Mutations — one-shot command execution
// ---------------------------------------------------------------------------

/** Execute a raw command in the debug sidecar */
export function useTMMDebugExec(clusterId: number) {
  return useAppMutation({
    mutationFn: (data: TMMDebugExecRequest) => tmmDebugApi.exec(clusterId, data),
  });
}

/** Execute a structured tmctl query */
export function useTMMDebugTmctl(clusterId: number) {
  return useAppMutation({
    mutationFn: (data: TMMDebugTmctlRequest) => tmmDebugApi.tmctl(clusterId, data),
  });
}

/** Execute configview uuid to inspect a CR config */
export function useTMMDebugConfigview(clusterId: number) {
  return useAppMutation({
    mutationFn: (data: TMMDebugConfigviewRequest) => tmmDebugApi.configview(clusterId, data),
  });
}

/** Discover available configview UUIDs */
export function useTMMDebugConfigviewUuids(clusterId: number) {
  return useAppMutation({
    mutationFn: (data: TMMDebugConfigviewUuidsRequest) =>
      tmmDebugApi.configviewUuids(clusterId, data),
  });
}

/** Execute bdt_cli networking diagnostic */
export function useTMMDebugBdt(clusterId: number) {
  return useAppMutation({
    mutationFn: (data: TMMDebugBdtRequest) => tmmDebugApi.bdt(clusterId, data),
  });
}
