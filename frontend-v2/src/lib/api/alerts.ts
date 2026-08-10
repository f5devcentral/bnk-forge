/**
 * Alert Channels API methods
 */
import { apiClient } from './client';
import type { AlertChannel, AlertChannelCreate, AlertHistoryEntry } from '@/types';

export const alertsApi = {
  // Alert Channels (Sprint 7)
  listAlertChannels: (params?: { enabled?: boolean; channel_type?: string }) =>
    apiClient.get<AlertChannel[]>('/api/alert-channels', { params }).then((res) => res.data),

  getAlertChannel: (id: number) =>
    apiClient.get<AlertChannel>(`/api/alert-channels/${id}`).then((res) => res.data),

  createAlertChannel: (data: AlertChannelCreate) =>
    apiClient.post<AlertChannel>('/api/alert-channels', data).then((res) => res.data),

  updateAlertChannel: (id: number, data: Partial<AlertChannelCreate>) =>
    apiClient.put<AlertChannel>(`/api/alert-channels/${id}`, data).then((res) => res.data),

  deleteAlertChannel: (id: number) =>
    apiClient.delete(`/api/alert-channels/${id}`).then((res) => res.data),

  testAlertChannel: (id: number) =>
    apiClient.post<{ success: boolean; message: string; error?: string; duration_ms?: number }>(
      `/api/alert-channels/${id}/test`
    ).then((res) => res.data),

  toggleAlertChannel: (id: number) =>
    apiClient.post<{ success: boolean; enabled: boolean; message: string }>(
      `/api/alert-channels/${id}/toggle`
    ).then((res) => res.data),

  getRecentAlerts: (params?: { limit?: number; event_type?: string; severity?: string }) =>
    apiClient.get<AlertHistoryEntry[]>('/api/alert-channels/history/recent', { params }).then((res) => res.data),
};
