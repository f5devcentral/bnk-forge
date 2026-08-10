import { useQuery, useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { notificationsApi } from '@/lib/api/notifications';
import type { NotificationData } from '@/lib/api/notifications';
import { queryKeys } from '@/lib/queryKeys';
import { QUERY_STALE_TIME, POLL_INTERVALS } from '@/lib/constants';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export type { NotificationData as Notification } from '@/lib/api/notifications';

export function useNotifications(unreadOnly: boolean = false) {
  return useQuery<NotificationData[]>({
    queryKey: queryKeys.notifications.list(unreadOnly),
    queryFn: () => notificationsApi.getNotifications({ unread_only: unreadOnly, limit: 50 }),
    staleTime: QUERY_STALE_TIME.DEFAULT,
    refetchInterval: POLL_INTERVALS.SLOW,
  });
}

export function useUnreadCount() {
  return useQuery({
    queryKey: queryKeys.notifications.unreadCount(),
    queryFn: async () => {
      const data = await notificationsApi.getUnreadCount();
      return data.count;
    },
    staleTime: QUERY_STALE_TIME.DEFAULT_MEDIUM,
    refetchInterval: POLL_INTERVALS.MEDIUM,
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (notificationId: number) => notificationsApi.markRead(notificationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.notifications.all });
    },
  });
}

export function useMarkAllRead() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: () => notificationsApi.markAllRead(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.notifications.all });
    },
  });
}

export function useDeleteNotification() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (notificationId: number) => notificationsApi.deleteNotification(notificationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.notifications.all });
    },
  });
}

const BELL_PAGE_SIZE = 20;

export interface NotificationFilters {
  unreadOnly?: boolean;
  category?: string;
  severity?: string;
}

/**
 * Cursor-paginated notifications query for the bell popover.
 * Uses `before_id` for cursor (newest-first); a page returning < BELL_PAGE_SIZE items is the last page.
 * Does NOT replace useNotifications — existing callers are unaffected.
 */
export function useNotificationsInfinite(filters: NotificationFilters = {}) {
  return useInfiniteQuery<NotificationData[], Error, { pages: NotificationData[][] }, ReturnType<typeof queryKeys.notifications.infinite>, number | undefined>({
    queryKey: queryKeys.notifications.infinite(filters),
    queryFn: ({ pageParam }) =>
      notificationsApi.getNotifications({
        unread_only: filters.unreadOnly,
        category: filters.category,
        severity: filters.severity,
        limit: BELL_PAGE_SIZE,
        before_id: pageParam,
      }),
    initialPageParam: undefined,
    getNextPageParam: (lastPage) => {
      if (lastPage.length < BELL_PAGE_SIZE) return undefined;
      return lastPage[lastPage.length - 1].id;
    },
    staleTime: QUERY_STALE_TIME.DEFAULT,
    refetchInterval: POLL_INTERVALS.SLOW,
  });
}
