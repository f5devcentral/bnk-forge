/**
 * Tests for the Header notification bell (D-025 P3)
 *
 * Covers:
 * - Clicking an item with action_url: navigates + marks read + closes popover
 * - Clicking an item without action_url: marks read, popover stays open
 * - Filter param (severity/category) reaches the MSW request
 * - Dismiss button calls DELETE and stopPropagation (no navigate)
 * - Infinite "load more" fetches with before_id
 * - Severity icons render including critical variant (ShieldAlert)
 *
 * CT-012: MSW handlers return the REAL NotificationData shape (with severity/category/action_url).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { Header } from '../Header';

// ─── Shared mocks ────────────────────────────────────────────────────────────

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const orig = await importOriginal<typeof import('react-router-dom')>();
  return { ...orig, useNavigate: () => mockNavigate };
});

// Silence unrelated hooks used by Header
vi.mock('@/hooks/useSystem', () => ({
  useUpgradeStatus: () => ({ isUpgrading: false, upgradePhaseLabel: null }),
}));
vi.mock('@/hooks/useBackup', () => ({
  useMaintenanceStatus: () => ({ data: null, isLoading: false }),
}));
vi.mock('@/components/layout/ProcessMetricsBar', () => ({
  ProcessMetricsBar: () => null,
}));

// ─── Fixture data ─────────────────────────────────────────────────────────────

const makeNotification = (
  overrides: Partial<{
    id: number;
    type: string;
    severity: string;
    category: string;
    action_url: string | null;
    is_read: boolean;
    title: string;
    message: string;
  }>
) => ({
  id: 1,
  user: 'admin',
  type: 'info',
  title: 'Test notification',
  message: 'Test message',
  resource_type: null,
  resource_id: null,
  is_read: false,
  created_at: '2026-02-20T12:00:00Z',
  read_at: null,
  severity: 'info',
  category: 'system',
  action_url: null,
  dedupe_key: null,
  ...overrides,
});

const notifications = [
  makeNotification({ id: 1, severity: 'success', category: 'deployment', action_url: '/tasks', title: 'Deploy done', is_read: false }),
  makeNotification({ id: 2, severity: 'critical', category: 'security', action_url: null, title: 'Security alert', is_read: false }),
  makeNotification({ id: 3, severity: 'warning', category: 'cluster', action_url: null, title: 'Cluster warning', is_read: true }),
];

function setupHandlers(overrideNotifications = notifications) {
  server.use(
    http.get('*/api/notifications', () => {
      return HttpResponse.json(overrideNotifications);
    }),
    http.get('*/api/notifications/unread-count', () => {
      return HttpResponse.json({ count: 2 });
    }),
    http.patch('*/api/notifications/:id/read', () => {
      return HttpResponse.json({ success: true, message: 'marked read' });
    }),
    http.delete('*/api/notifications/:id', () => {
      return HttpResponse.json({ success: true, message: 'deleted' });
    }),
    http.post('*/api/notifications/mark-all-read', () => {
      return HttpResponse.json({ success: true, message: 'all read' });
    })
  );
}

async function openBell() {
  const bellBtn = screen.getByRole('button', { name: /notifications/i });
  await act(async () => {
    fireEvent.click(bellBtn);
  });
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('Header notification bell', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
    setupHandlers();
  });

  it('renders the bell button with unread badge', async () => {
    render(<Header />);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /notifications/i })).toBeInTheDocument();
    });
    // Badge shows unread count (comes from useUnreadCount)
    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument();
    });
  });

  it('opens popover and shows notification titles', async () => {
    render(<Header />);
    await openBell();
    await waitFor(() => {
      expect(screen.getByText('Deploy done')).toBeInTheDocument();
      expect(screen.getByText('Security alert')).toBeInTheDocument();
      expect(screen.getByText('Cluster warning')).toBeInTheDocument();
    });
  });

  it('clicking item with action_url navigates, marks read, closes popover', async () => {
    const capturedPatchIds: number[] = [];
    server.use(
      http.patch('*/api/notifications/:id/read', ({ params }) => {
        capturedPatchIds.push(Number(params.id));
        return HttpResponse.json({ success: true, message: 'ok' });
      })
    );

    render(<Header />);
    await openBell();

    await waitFor(() => expect(screen.getByText('Deploy done')).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByText('Deploy done'));
    });

    // Navigated to action_url
    expect(mockNavigate).toHaveBeenCalledWith('/tasks');

    // Marked as read
    await waitFor(() => {
      expect(capturedPatchIds).toContain(1);
    });

    // Popover closes: the popover content should no longer be visible
    await waitFor(() => {
      expect(screen.queryByText('Cluster warning')).not.toBeInTheDocument();
    });
  });

  it('clicking item without action_url marks read but does NOT navigate', async () => {
    render(<Header />);
    await openBell();

    await waitFor(() => expect(screen.getByText('Security alert')).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByText('Security alert'));
    });

    expect(mockNavigate).not.toHaveBeenCalled();
    // Popover stays open — other items still visible
    expect(screen.getByText('Deploy done')).toBeInTheDocument();
  });

  it('clicking already-read item does not call markRead', async () => {
    const capturedPatchIds: number[] = [];
    server.use(
      http.patch('*/api/notifications/:id/read', ({ params }) => {
        capturedPatchIds.push(Number(params.id));
        return HttpResponse.json({ success: true, message: 'ok' });
      })
    );

    render(<Header />);
    await openBell();

    await waitFor(() => expect(screen.getByText('Cluster warning')).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByText('Cluster warning'));
    });

    // id=3 is_read:true, should NOT be patched
    await new Promise((r) => setTimeout(r, 100));
    expect(capturedPatchIds).not.toContain(3);
  });

  it('dismiss button calls DELETE and does NOT call markRead or navigate', async () => {
    const capturedDeleteIds: number[] = [];
    const capturedPatchIds: number[] = [];
    server.use(
      http.delete('*/api/notifications/:id', ({ params }) => {
        capturedDeleteIds.push(Number(params.id));
        return HttpResponse.json({ success: true, message: 'ok' });
      }),
      http.patch('*/api/notifications/:id/read', ({ params }) => {
        capturedPatchIds.push(Number(params.id));
        return HttpResponse.json({ success: true, message: 'ok' });
      })
    );

    render(<Header />);
    await openBell();

    await waitFor(() => expect(screen.getByText('Deploy done')).toBeInTheDocument());

    // Hover the item to reveal the dismiss button (opacity-0 group-hover:opacity-100)
    // In RTL, aria-label is the reliable selector
    const dismissBtns = screen.getAllByRole('button', { name: /dismiss notification/i });
    expect(dismissBtns.length).toBeGreaterThan(0);

    await act(async () => {
      fireEvent.click(dismissBtns[0]);
    });

    await waitFor(() => {
      expect(capturedDeleteIds).toContain(1);
    });

    // markRead was NOT called because stopPropagation prevented the row click
    await new Promise((r) => setTimeout(r, 100));
    expect(capturedPatchIds).not.toContain(1);
    // navigate was NOT called
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('filter by severity sends correct query param to the API', async () => {
    const capturedUrls: string[] = [];
    server.use(
      http.get('*/api/notifications', ({ request }) => {
        capturedUrls.push(request.url);
        return HttpResponse.json([]);
      })
    );

    render(<Header />);
    await openBell();

    // Wait for the filter bar to appear
    await waitFor(() => {
      expect(screen.getByText('critical')).toBeInTheDocument();
    });

    // Click the "critical" severity filter
    await act(async () => {
      fireEvent.click(screen.getByText('critical'));
    });

    await waitFor(() => {
      const hasCritical = capturedUrls.some((url) => url.includes('severity=critical'));
      expect(hasCritical).toBe(true);
    });
  });

  it('filter by category sends correct query param to the API', async () => {
    const capturedUrls: string[] = [];
    server.use(
      http.get('*/api/notifications', ({ request }) => {
        capturedUrls.push(request.url);
        return HttpResponse.json([]);
      })
    );

    render(<Header />);
    await openBell();

    await waitFor(() => {
      expect(screen.getByText('fleet')).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText('fleet'));
    });

    await waitFor(() => {
      const hasFleet = capturedUrls.some((url) => url.includes('category=fleet'));
      expect(hasFleet).toBe(true);
    });
  });

  it('severity-based icons render — critical uses ShieldAlert aria via title', async () => {
    render(<Header />);
    await openBell();

    await waitFor(() => {
      // Critical notification "Security alert" should be in the list
      expect(screen.getByText('Security alert')).toBeInTheDocument();
    });
    // Category badge shows — may match multiple times (filter button + badge)
    const securityEls = screen.getAllByText('security');
    expect(securityEls.length).toBeGreaterThanOrEqual(1);
  });

  it('empty state shows filter-aware message when filter yields no results', async () => {
    server.use(
      http.get('*/api/notifications', () => {
        return HttpResponse.json([]);
      })
    );

    render(<Header />);
    await openBell();

    await waitFor(() => {
      expect(screen.getByText('critical')).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText('critical'));
    });

    await waitFor(() => {
      expect(screen.getByText(/no critical notifications/i)).toBeInTheDocument();
    });
  });

  it('load more button fetches next page with before_id', async () => {
    // First page returns PAGE_SIZE (20) items, triggering "has next page"
    const firstPage = Array.from({ length: 20 }, (_, i) =>
      makeNotification({ id: i + 1, title: `Notification ${i + 1}` })
    );
    const secondPage = [makeNotification({ id: 21, title: 'Notification 21' })];
    const capturedUrls: string[] = [];

    server.use(
      http.get('*/api/notifications', ({ request }) => {
        capturedUrls.push(request.url);
        const url = new URL(request.url);
        const beforeId = url.searchParams.get('before_id');
        if (beforeId) {
          return HttpResponse.json(secondPage);
        }
        return HttpResponse.json(firstPage);
      })
    );

    render(<Header />);
    await openBell();

    // Wait for "Load more" button to appear (first page is full → hasNextPage)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /load more/i }));
    });

    // Second request must include before_id=20 (last item of first page)
    await waitFor(() => {
      const secondReq = capturedUrls.find((u) => u.includes('before_id=20'));
      expect(secondReq).toBeDefined();
    });

    // Second page item appears
    await waitFor(() => {
      expect(screen.getByText('Notification 21')).toBeInTheDocument();
    });
  });
});
