/**
 * Audit Log API methods
 */
import { apiClient } from './client';

export const auditApi = {
  /** Get audit log entries with filtering and pagination */
  getLogs: (params?: {
    page?: number;
    page_size?: number;
    action?: string;
    resource_type?: string;
    status?: string;
    user?: string;
    search?: string;
  }) =>
    apiClient.get('/api/audit', { params }).then((res) => res.data),

  /** Get available filter options for audit logs */
  getFilters: () =>
    apiClient.get('/api/audit/filters').then((res) => res.data),

  /** Get audit statistics for the last N days */
  getStats: (days: number = 7) =>
    apiClient.get('/api/audit/stats', { params: { days } }).then((res) => res.data),
};
