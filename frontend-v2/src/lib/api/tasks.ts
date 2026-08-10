/**
 * Tasks API methods
 */
import { apiClient } from './client';
import type { Task, TaskListResponse, TaskStatsResponse } from '@/types';

export const tasksApi = {
  // Task Management
  getTasks: (params?: {
    project_id?: number;
    module_id?: number;
    status?: string;
    task_type?: string;
    archived?: boolean;
    limit?: number;
    offset?: number;
  }) =>
    apiClient.get<TaskListResponse>('/api/tasks', { params }).then((res) => res.data),

  getTask: (taskId: number, options?: { log_tail?: number; log_max_size?: number }) =>
    apiClient.get<Task>(`/api/tasks/${taskId}`, { params: options }).then((res) => res.data),

  cancelTask: (taskId: number) =>
    apiClient.post<{ success: boolean; message: string; task_id: number }>(`/api/tasks/${taskId}/cancel`).then((res) => res.data),

  getTaskStats: (params?: { project_id?: number; days?: number }) =>
    apiClient.get<TaskStatsResponse>('/api/tasks/stats/summary', { params }).then((res) => res.data),

  cleanupOldTasks: (days?: number) =>
    apiClient.delete<{ success: boolean; deleted_count: number; cutoff_date: string }>('/api/tasks/cleanup', { params: { days } }).then((res) => res.data),

  deleteTask: (taskId: number) =>
    apiClient.delete<{ success: boolean; task_id: number; deleted: boolean }>(`/api/tasks/${taskId}`).then((res) => res.data),

  archiveTask: (taskId: number, archived = true) =>
    apiClient.post<{ success: boolean; task_id: number; archived: boolean }>(`/api/tasks/${taskId}/archive`, null, { params: { archived } }).then((res) => res.data),

  bulkDeleteTasks: (taskIds: number[]) =>
    apiClient.post<{ success: boolean; deleted_count: number }>('/api/tasks/bulk-delete', { task_ids: taskIds }).then((res) => res.data),

  bulkArchiveTasks: (taskIds: number[], archived = true) =>
    apiClient.post<{ success: boolean; updated_count: number; archived: boolean }>('/api/tasks/bulk-archive', { task_ids: taskIds }, { params: { archived } }).then((res) => res.data),
};
