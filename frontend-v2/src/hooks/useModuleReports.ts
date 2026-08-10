/**
 * Module reports hooks (D-034 PR-2.5 — tool-written run/scenario reports).
 *
 * useModuleReports lists the report runs a module's tool wrote into its
 * workspace (empty list for modules that declare no reports block);
 * useModuleReportContent reads one selected file (enabled only when a file
 * path is provided). Reports are static artifacts of a completed run, so
 * neither hook polls.
 */
import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

export function useModuleReports(moduleId: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.modules.project.reports(moduleId),
    queryFn: () => api.getModuleReports(moduleId),
    enabled: !!moduleId && (options?.enabled ?? true),
    staleTime: 30_000,
  });
}

export function useModuleReportContent(moduleId: number, path: string | null) {
  return useQuery({
    queryKey: queryKeys.modules.project.reportContent(moduleId, path ?? ''),
    queryFn: () => api.getModuleReportContent(moduleId, path as string),
    // Content is only fetched once a file is selected.
    enabled: !!moduleId && !!path,
    staleTime: 5 * 60_000,
  });
}
