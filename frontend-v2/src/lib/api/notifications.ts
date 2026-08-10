/**
 * Notifications API methods
 */
import { apiClient } from './client';

export interface NotificationData {
  id: number;
  user: string;
  type: 'success' | 'error' | 'info' | 'warning';
  title: string;
  message: string;
  resource_type?: string;
  resource_id?: number;
  is_read: boolean;
  created_at: string;
  read_at?: string;
  severity: string;
  category: string;
  action_url: string | null;
  dedupe_key: string | null;
}

export interface NotificationCreatePayload {
  type?: string;
  title: string;
  message: string;
  resource_type?: string;
  resource_id?: number;
  severity?: string;
  category?: string;
  action_url?: string;
  dedupe_key?: string;
}

export interface NotificationListParams {
  unread_only?: boolean;
  limit?: number;
  category?: string;
  severity?: string;
  before_id?: number;
}

export const notificationsApi = {
  /** Get notifications for the current user */
  getNotifications: (params?: NotificationListParams) =>
    apiClient.get<NotificationData[]>('/api/notifications', { params }).then((res) => res.data),

  /** Get unread notification count */
  getUnreadCount: () =>
    apiClient.get<{ count: number }>('/api/notifications/unread-count').then((res) => res.data),

  /** Mark a notification as read */
  markRead: (notificationId: number) =>
    apiClient.patch<{ success: boolean; message: string }>(`/api/notifications/${notificationId}/read`).then((res) => res.data),

  /** Mark all notifications as read */
  markAllRead: () =>
    apiClient.post<{ success: boolean; message: string }>('/api/notifications/mark-all-read').then((res) => res.data),

  /** Delete a notification */
  deleteNotification: (notificationId: number) =>
    apiClient.delete<{ success: boolean; message: string }>(`/api/notifications/${notificationId}`).then((res) => res.data),

  /** Create a notification */
  createNotification: (data: NotificationCreatePayload) =>
    apiClient.post<NotificationData>('/api/notifications', data).then((res) => res.data),
};
