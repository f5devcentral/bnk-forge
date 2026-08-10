/**
 * useConnectivity — single React Query hook backing the SSE-driven
 * reachability cache.
 *
 * Why this shape (not "EventSource as a React Query hook"):
 *   The SSE stream and React Query have different lifecycles. The stream
 *   should be one global subscription, kept open for the whole session.
 *   React Query is a per-render cache. We bridge them by patching events
 *   into the cache via `queryClient.setQueryData`, then having components
 *   read from the cache through this hook.
 *
 * The bridge is started exactly once by `useConnectivitySession()` (mount
 * at the top of the app). Per-component callers use `useConnectivity()` /
 * `useTargetConnectivity()` and never touch the SSE stream directly.
 */
import { useEffect, useMemo, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  forceProbe,
  getInitialState,
  reachabilityKey,
  watchConnectivity,
  type ReachabilityState,
} from '@/lib/api/connectivity';

const QUERY_KEY = ['connectivity', 'state'] as const;

type StateMap = Record<string, ReachabilityState>;

function applyEvent(prev: StateMap | undefined, event: ReachabilityState): StateMap {
  const next: StateMap = { ...(prev ?? {}) };
  next[reachabilityKey(event.target_type, event.target_id)] = event;
  return next;
}

function statesToMap(arr: ReachabilityState[]): StateMap {
  const out: StateMap = {};
  for (const s of arr) {
    out[reachabilityKey(s.target_type, s.target_id)] = s;
  }
  return out;
}

/**
 * Bootstraps the SSE subscription + initial-state fetch. Mount once near
 * the top of the app (e.g. inside the authenticated layout). Re-mounting
 * is safe — the ref guard prevents duplicate streams.
 */
export function useConnectivitySession(): void {
  const queryClient = useQueryClient();
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    const controller = new AbortController();

    // Seed cache with one snapshot fetch so first paint isn't blank if SSE
    // takes a beat to deliver.
    void getInitialState()
      .then((states) => {
        queryClient.setQueryData<StateMap>(QUERY_KEY, statesToMap(states));
      })
      .catch(() => {
        // Snapshot is best-effort — SSE will populate when it connects.
      });

    const stop = watchConnectivity({
      signal: controller.signal,
      onState: (event) => {
        queryClient.setQueryData<StateMap>(QUERY_KEY, (prev) =>
          applyEvent(prev, event),
        );
      },
    });

    return () => {
      controller.abort();
      stop();
      startedRef.current = false;
    };
  }, [queryClient]);
}

export interface UseConnectivityResult {
  states: StateMap;
  isLoading: boolean;
  retry: (targetType: string, targetId: number) => Promise<void>;
}

/**
 * Read the entire connectivity map from the cache. Re-renders on any change.
 */
export function useConnectivity(): UseConnectivityResult {
  const queryClient = useQueryClient();
  const query = useQuery<StateMap>({
    queryKey: QUERY_KEY,
    queryFn: () => getInitialState().then(statesToMap),
    // Snapshot fetch is cheap; SSE drives subsequent updates so disable
    // the query's own refetch behavior.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const retry = async (targetType: string, targetId: number) => {
    const state = await forceProbe(targetType, targetId);
    queryClient.setQueryData<StateMap>(QUERY_KEY, (prev) =>
      applyEvent(prev, state),
    );
  };

  return {
    states: query.data ?? {},
    isLoading: query.isLoading,
    retry,
  };
}

/**
 * Returns false if the named target is currently UNREACHABLE. Returns true
 * for REACHABLE or UNKNOWN — the cold-start case stays enabled so the
 * child's own loading state (not the gate's) drives first paint.
 *
 * Use as a defense-in-depth gate on React Query hooks even when callers
 * also wrap the surrounding panel in <ConnectivityGate>:
 *
 *     const reachable = useClusterReachable(clusterId);
 *     return useQuery({ ..., enabled: reachable && !!clusterId });
 */
export function useClusterReachable(clusterId: number | undefined): boolean {
  const { isUnreachable } = useTargetConnectivity('cluster', clusterId);
  return !isUnreachable;
}

/**
 * Read connectivity for a single target.
 */
export function useTargetConnectivity(
  targetType: string,
  targetId: number | undefined,
): {
  state: ReachabilityState | undefined;
  isReachable: boolean;
  isUnreachable: boolean;
  isUnknown: boolean;
  retry: () => Promise<void>;
} {
  const { states, retry } = useConnectivity();
  const state = useMemo(() => {
    if (targetId === undefined || targetId === null) return undefined;
    return states[reachabilityKey(targetType, targetId)];
  }, [states, targetType, targetId]);

  // Cold start (no state yet) renders as UNKNOWN, NOT REACHABLE — fail
  // honestly so the UI shows a checking… state instead of fail-open.
  const value = state?.state ?? 'unknown';
  return {
    state,
    isReachable: value === 'reachable',
    isUnreachable: value === 'unreachable',
    isUnknown: value === 'unknown',
    retry: async () => {
      if (targetId !== undefined && targetId !== null) {
        await retry(targetType, targetId);
      }
    },
  };
}
