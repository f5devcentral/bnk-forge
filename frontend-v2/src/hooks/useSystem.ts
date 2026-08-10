/**
 * React Query hooks for system administration and monitoring
 * 
 * PERFORMANCE OPTIMIZATION (ADR-019):
 * - Reduced polling intervals to prevent API overload
 * - Added visibility-based polling (disabled when tab hidden)
 * - Previous aggressive polling (5s/10s) caused 32+ second latency
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { QUERY_STALE_TIME, POLL_INTERVALS } from '@/lib/constants';
import type { CleanupType, VersionInfo, UpgradeResponse } from '@/types/system';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

/**
 * Hook to track document visibility state
 * Used to disable polling when browser tab is hidden (ADR-019)
 */
function useDocumentVisibility(): boolean {
  const [isVisible, setIsVisible] = useState(() => 
    typeof document !== 'undefined' ? !document.hidden : true
  );

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsVisible(!document.hidden);
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, []);

  return isVisible;
}

/**
 * Fetch system health status
 * 
 * PERFORMANCE: Reduced from 10s to 60s polling, disabled when tab hidden
 * The backend now has a 3-second timeout on Celery inspect calls (ADR-019)
 * PERF-014: Increased staleTime to 120s since backend caches for 30s anyway
 */
export function useSystemHealth() {
  const isVisible = useDocumentVisibility();
  
  return useQuery({
    queryKey: queryKeys.system.health(),
    queryFn: () => api.getSystemHealth(),
    refetchInterval: isVisible ? POLL_INTERVALS.VERY_SLOW : false, // disabled when tab hidden
    staleTime: QUERY_STALE_TIME.SYSTEM, // PERF-014: 2 minutes
  });
}

/**
 * Fetch task queue metrics
 * 
 * PERFORMANCE: Reduced from 5s to 30s polling, disabled when tab hidden
 * This endpoint makes multiple Celery inspect calls which can be slow (ADR-019)
 * PERF-014: Increased staleTime to 120s since backend caches for 30s anyway
 */
export function useQueueMetrics() {
  const isVisible = useDocumentVisibility();
  
  return useQuery({
    queryKey: queryKeys.system.queueMetrics(),
    queryFn: () => api.getQueueMetrics(),
    refetchInterval: isVisible ? POLL_INTERVALS.SLOW : false, // disabled when tab hidden
    staleTime: QUERY_STALE_TIME.SYSTEM, // PERF-014: 2 minutes
  });
}

/**
 * Fetch performance metrics
 * 
 * PERFORMANCE: Increased from 30s to 60s polling, disabled when tab hidden
 * PERF-014: Increased staleTime to 120s
 */
export function usePerformanceMetrics() {
  const isVisible = useDocumentVisibility();
  
  return useQuery({
    queryKey: queryKeys.system.performance(),
    queryFn: () => api.getPerformanceMetrics(),
    refetchInterval: isVisible ? POLL_INTERVALS.VERY_SLOW : false, // disabled when tab hidden
    staleTime: QUERY_STALE_TIME.SYSTEM, // PERF-014: 2 minutes
  });
}

/**
 * Fetch recent errors
 * 
 * PERFORMANCE: Increased from 15s to 60s polling, disabled when tab hidden
 * PERF-014: Increased staleTime to 120s
 */
export function useRecentErrors(limit = 10) {
  const isVisible = useDocumentVisibility();
  
  return useQuery({
    queryKey: queryKeys.system.errors(limit),
    queryFn: () => api.getRecentErrors(limit),
    refetchInterval: isVisible ? POLL_INTERVALS.VERY_SLOW : false, // disabled when tab hidden
    staleTime: QUERY_STALE_TIME.SYSTEM, // PERF-014: 2 minutes
  });
}

/**
 * Fetch database statistics (manual refresh only)
 */
export function useDatabaseStats() {
  return useQuery({
    queryKey: queryKeys.system.databaseStats(),
    queryFn: () => api.getDatabaseStats(),
    // No auto-refresh - manual only
  });
}

/**
 * Cleanup database mutation
 */
export function useCleanupDatabase() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ type, olderThanDays }: { type: CleanupType; olderThanDays: number }) =>
      api.cleanupDatabase(type, olderThanDays),
    onSuccess: () => {
      // Invalidate database stats to refresh after cleanup
      queryClient.invalidateQueries({ queryKey: queryKeys.system.databaseStats() });
      queryClient.invalidateQueries({ queryKey: queryKeys.system.performance() });
    },
  });
}

/**
 * Vacuum database mutation
 */
export function useVacuumDatabase() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: () => api.vacuumDatabase(),
    onSuccess: () => {
      // Invalidate database stats to refresh after vacuum
      queryClient.invalidateQueries({ queryKey: queryKeys.system.databaseStats() });
      queryClient.invalidateQueries({ queryKey: queryKeys.system.performance() });
    },
  });
}

/**
 * Fetch container status
 * 
 * PERFORMANCE: Reduced from 10s to 30s polling, disabled when tab hidden
 * PERF-014: Increased staleTime to 120s
 */
export function useContainerStatus() {
  const isVisible = useDocumentVisibility();
  
  return useQuery({
    queryKey: queryKeys.system.containerStatus(),
    queryFn: () => api.getContainerStatus(),
    refetchInterval: isVisible ? POLL_INTERVALS.SLOW : false, // disabled when tab hidden
    staleTime: QUERY_STALE_TIME.SYSTEM, // PERF-014: 2 minutes
  });
}

/**
 * Restart containers mutation
 */
export function useRestartContainers() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (services?: string[]) => api.restartContainers(services),
    onSuccess: () => {
      // Invalidate container status to refresh after restart
      queryClient.invalidateQueries({ queryKey: queryKeys.system.containerStatus() });
      queryClient.invalidateQueries({ queryKey: queryKeys.system.health() });
    },
  });
}

/**
 * UP-013: Fetch system version info (current, latest, update availability)
 */
export function useSystemVersion() {
  return useQuery<VersionInfo>({
    queryKey: queryKeys.system.version(),
    queryFn: () => api.getSystemVersion(),
    staleTime: 30_000,
  });
}

/**
 * UP-013: Trigger a system upgrade mutation
 */
export function useSystemUpgrade() {
  const queryClient = useQueryClient();

  return useAppMutation<UpgradeResponse, Error>({
    mutationFn: () => api.triggerSystemUpgrade(),
    onSuccess: () => {
      // Invalidate upgrade status so the banner appears
      queryClient.invalidateQueries({ queryKey: queryKeys.system.upgradeStatus() });
    },
  });
}

/**
 * UP-012: Global upgrade status — used by Header banner and deploy buttons
 *
 * Polls upgrade status every 10s to detect when an upgrade starts/completes.
 * Returns isUpgrading boolean + full state for display purposes.
 */
export function useUpgradeStatus() {
  const isVisible = useDocumentVisibility();

  const query = useQuery({
    queryKey: queryKeys.system.upgradeStatus(),
    queryFn: () => api.getUpgradeStatus(),
    refetchInterval: isVisible ? 10_000 : false,
    staleTime: 5_000,
    retry: false, // Don't retry if backend is down (may be restarting during upgrade)
  });

  const isUpgrading = query.data?.status === 'in_progress';
  const upgradePhase = query.data?.current_phase ?? null;
  const upgradePhaseLabel = query.data?.phase_label ?? null;

  return {
    ...query,
    isUpgrading,
    upgradePhase,
    upgradePhaseLabel,
  };
}
