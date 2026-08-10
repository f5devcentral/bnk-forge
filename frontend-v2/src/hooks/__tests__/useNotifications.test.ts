/**
 * Tests for useNotifications hooks
 *
 * Covers: useNotifications (list query with unreadOnly filter),
 * useUnreadCount query, useMarkNotificationRead / useMarkAllRead /
 * useDeleteNotification mutations, and error handling.
 *
 * Note: These hooks use apiClient.get directly (not the `api` facade),
 * so all handlers are added via server.use in beforeEach.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useNotifications,
  useUnreadCount,
  useMarkNotificationRead,
  useMarkAllRead,
  useDeleteNotification,
} from '@/hooks/useNotifications';
import React from 'react';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

const mockNotifications = [
  {
    id: 1,
    user: 'admin',
    type: 'success',
    title: 'Deploy complete',
    message: 'VPC module applied successfully',
    resource_type: 'module',
    resource_id: 1,
    is_read: false,
    created_at: '2026-02-20T12:00:00Z',
  },
  {
    id: 2,
    user: 'admin',
    type: 'info',
    title: 'Drift detected',
    message: 'Configuration drift found in EKS module',
    resource_type: 'module',
    resource_id: 2,
    is_read: true,
    created_at: '2026-02-19T10:00:00Z',
    read_at: '2026-02-19T11:00:00Z',
  },
  {
    id: 3,
    user: 'admin',
    type: 'warning',
    title: 'Cost alert',
    message: 'Monthly cost exceeded threshold',
    is_read: false,
    created_at: '2026-02-18T08:00:00Z',
  },
];

beforeEach(() => {
  server.use(
    http.get('*/api/notifications', ({ request }) => {
      const url = new URL(request.url);
      const unreadOnly = url.searchParams.get('unread_only');
      if (unreadOnly === 'true') {
        return HttpResponse.json(mockNotifications.filter((n) => !n.is_read));
      }
      return HttpResponse.json(mockNotifications);
    }),

    http.get('*/api/notifications/unread-count', () => {
      return HttpResponse.json({ count: 3 });
    }),

    http.patch('*/api/notifications/:id/read', ({ params }) => {
      return HttpResponse.json({ success: true, id: Number(params.id) });
    }),

    http.post('*/api/notifications/mark-all-read', () => {
      return HttpResponse.json({ success: true, updated: 3 });
    }),

    http.delete('*/api/notifications/:id', ({ params }) => {
      return HttpResponse.json({ success: true, id: Number(params.id) });
    })
  );
});

// ============================================================================
// useNotifications
// ============================================================================

describe('useNotifications', () => {
  it('fetches all notifications', async () => {
    const { result } = renderHook(() => useNotifications(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toHaveLength(3);
    expect(result.current.data![0].title).toBe('Deploy complete');
  });

  it('fetches only unread notifications when unreadOnly is true', async () => {
    const { result } = renderHook(() => useNotifications(true), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Only notifications with is_read: false
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data!.every((n) => !n.is_read)).toBe(true);
  });
});

// ============================================================================
// useUnreadCount
// ============================================================================

describe('useUnreadCount', () => {
  it('fetches the unread notification count', async () => {
    const { result } = renderHook(() => useUnreadCount(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBe(3);
  });
});

// ============================================================================
// useMarkNotificationRead
// ============================================================================

describe('useMarkNotificationRead', () => {
  it('marks a notification as read', async () => {
    const { result } = renderHook(() => useMarkNotificationRead(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toEqual({ success: true, id: 1 });
  });
});

// ============================================================================
// useMarkAllRead
// ============================================================================

describe('useMarkAllRead', () => {
  it('marks all notifications as read', async () => {
    const { result } = renderHook(() => useMarkAllRead(), {
      wrapper: createWrapper(),
    });

    result.current.mutate();

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toEqual({ success: true, updated: 3 });
  });
});

// ============================================================================
// useDeleteNotification
// ============================================================================

describe('useDeleteNotification', () => {
  it('deletes a notification', async () => {
    const { result } = renderHook(() => useDeleteNotification(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(2);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toEqual({ success: true, id: 2 });
  });
});

// ============================================================================
// Error handling
// ============================================================================

describe('error handling', () => {
  it('surfaces server errors on notifications fetch', async () => {
    server.use(
      http.get('*/api/notifications', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Service unavailable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useNotifications(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
    expect(result.current.data).toBeUndefined();
  });
});
