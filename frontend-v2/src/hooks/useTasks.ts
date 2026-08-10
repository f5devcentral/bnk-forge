import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { notify } from '@/lib/notify';
import { POLL_INTERVALS } from '@/lib/constants';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export function useTasks(params?: {
  project_id?: number;
  module_id?: number;
  status?: string;
  task_type?: string;
  archived?: boolean;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: ['tasks', params],
    queryFn: () => api.getTasks(params),
    // Reduced polling - WebSocket provides real-time updates
    // Polling only as fallback in case WebSocket fails
    refetchInterval: POLL_INTERVALS.VERY_SLOW, // WebSocket provides real-time, polling as fallback
  });
}

export function useTask(taskId: number, options?: { log_tail?: number; log_max_size?: number }) {
  const queryClient = useQueryClient();

  // Default to showing last 1000 lines for performance
  const logOptions = {
    log_tail: options?.log_tail ?? 1000,
    log_max_size: options?.log_max_size ?? 200000,
  };

  // Track whether we've already invalidated for this task completion
  const invalidatedRef = { current: false };

  return useQuery({
    queryKey: ['tasks', taskId, logOptions],
    queryFn: async () => {
      const task = await api.getTask(taskId, logOptions);

      // Invalidate task list when task transitions to a terminal state
      if (
        task &&
        (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') &&
        !invalidatedRef.current
      ) {
        invalidatedRef.current = true;
        queryClient.invalidateQueries({ queryKey: ['tasks'], exact: false });
      }

      return task;
    },
    enabled: !!taskId,
    // WebSocket provides real-time status updates
    // Poll logs every 10s for active tasks (logs aren't sent via WebSocket)
    refetchInterval: (query) => {
      const task = query.state.data;

      // If task is active, poll every 10 seconds for log updates
      if (task && (task.status === 'in_progress' || task.status === 'queued')) {
        return 10000;
      }

      // Stop polling if task is completed, failed, or cancelled
      return false;
    },
  });
}

export function useCancelTask() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (taskId: number) => api.cancelTask(taskId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['tasks', data.task_id] });
      notify.success(data.message || 'Task cancelled successfully', undefined, { category: 'system' });
    },
  });
}

export function useTaskStats(params?: { project_id?: number; days?: number }) {
  return useQuery({
    queryKey: ['task-stats', params],
    queryFn: () => api.getTaskStats(params),
    staleTime: 30000, // 30 seconds - stats don't need instant refresh
  });
}

export function useCleanupOldTasks() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (days?: number) => api.cleanupOldTasks(days),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      notify.success(`Removed ${data.deleted_count} old operation(s)`, undefined, { category: 'system' });
    },
  });
}

export function useDeleteTask() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (taskId: number) => api.deleteTask(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      notify.success('Operation deleted', undefined, { category: 'system' });
    },
  });
}

export function useArchiveTask() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ taskId, archived }: { taskId: number; archived: boolean }) =>
      api.archiveTask(taskId, archived),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      notify.success(data.archived ? 'Operation archived' : 'Operation unarchived', undefined, {
        category: 'system',
      });
    },
  });
}

export function useBulkDeleteTasks() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (taskIds: number[]) => api.bulkDeleteTasks(taskIds),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      notify.success(`Deleted ${data.deleted_count} operation(s)`, undefined, { category: 'system' });
    },
  });
}

export function useBulkArchiveTasks() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ taskIds, archived }: { taskIds: number[]; archived: boolean }) =>
      api.bulkArchiveTasks(taskIds, archived),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      notify.success(
        `${data.archived ? 'Archived' : 'Unarchived'} ${data.updated_count} operation(s)`,
        undefined,
        { category: 'system' },
      );
    },
  });
}
