/**
 * Module actions hooks (D-034 — manifest-declared test/scenario actions).
 *
 * useModuleActions probes the actions a container module's artifact manifest
 * declares (empty list for non-container modules); useRunModuleAction submits
 * one. Action runs never change module.status — progress is tracked through
 * the Task record the submit response's task_id points at (useTask polling).
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { notify } from '@/lib/notify';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export function useModuleActions(moduleId: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.modules.project.actions(moduleId),
    queryFn: () => api.getModuleActions(moduleId),
    enabled: !!moduleId && (options?.enabled ?? true),
    // Declared actions are versioned with the pack (D-033 immutable versions) —
    // they only change when the module version changes.
    staleTime: 60_000,
  });
}

export function useRunModuleAction() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      moduleId,
      actionName,
      inputs,
    }: {
      moduleId: number;
      actionName: string;
      inputs?: Record<string, unknown>;
    }) => api.runModuleAction(moduleId, actionName, inputs),
    onSuccess: async (data) => {
      // Use refetchQueries so the fresh queued/in_progress task record is in
      // cache before the useTask/useTasks polling refetchInterval evaluators
      // run (actions never change module.status, so the task record is the
      // only progress signal).
      await queryClient.refetchQueries({ queryKey: ['tasks'] });

      notify.success(data.message || 'Action queued', 'Open the Tasks page to monitor progress', {
        category: 'deployment',
        action_url: '/tasks',
      });
    },
  });
}
