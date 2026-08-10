/**
 * Tests for AuditLog component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import AuditLog from '../AuditLog';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AuditLog', () => {
  it('renders audit log table with entries', async () => {
    server.use(
      http.get('*/api/audit', () =>
        HttpResponse.json({
          logs: [
            {
              id: 1,
              timestamp: '2026-01-01T10:00:00Z',
              user: 'admin',
              user_id: 1,
              action: 'create',
              resource_type: 'project',
              resource_id: '1',
              resource_name: 'test-project',
              status: 'success',
              details: {},
              ip_address: '127.0.0.1',
              http_method: 'POST',
              http_path: '/api/projects',
              http_status_code: 200,
              duration_ms: 50,
            },
          ],
          total: 1,
          page: 1,
          page_size: 25,
          total_pages: 1,
        })
      ),
      http.get('*/api/audit/filters', () =>
        HttpResponse.json({ actions: ['create'], resource_types: ['project'], users: ['admin'], statuses: ['success'] })
      ),
      http.get('*/api/audit/stats', () =>
        HttpResponse.json({
          period_days: 7,
          total_events: 42,
          actions: { create: 20, update: 15, delete: 7 },
          resource_types: { project: 30, module: 12 },
          users: { admin: 42 },
          statuses: { success: 40, failed: 2 },
          daily_activity: [],
        })
      ),
    );

    render(<AuditLog />);

    await waitFor(() => {
      expect(screen.getByText('admin')).toBeInTheDocument();
    });
    // Use getAllByText since filter dropdown may also contain 'create'
    expect(screen.getAllByText('create').length).toBeGreaterThan(0);
    expect(screen.getAllByText('success').length).toBeGreaterThan(0);
  });

  it('shows empty state when no audit events exist', async () => {
    server.use(
      http.get('*/api/audit', () =>
        HttpResponse.json({
          logs: [],
          total: 0,
          page: 1,
          page_size: 25,
          total_pages: 0,
        })
      ),
      http.get('*/api/audit/filters', () =>
        HttpResponse.json({ actions: [], resource_types: [], users: [], statuses: [] })
      ),
      http.get('*/api/audit/stats', () =>
        HttpResponse.json({
          period_days: 7,
          total_events: 0,
          actions: {},
          resource_types: {},
          users: {},
          statuses: {},
          daily_activity: [],
        })
      ),
    );

    render(<AuditLog />);

    await waitFor(() => {
      expect(screen.getByText('No audit events found')).toBeInTheDocument();
    });
  });

  it('renders stats cards when stats are available', async () => {
    server.use(
      http.get('*/api/audit', () =>
        HttpResponse.json({ logs: [], total: 0, page: 1, page_size: 25, total_pages: 0 })
      ),
      http.get('*/api/audit/filters', () =>
        HttpResponse.json({ actions: [], resource_types: [], users: [], statuses: [] })
      ),
      http.get('*/api/audit/stats', () =>
        HttpResponse.json({
          period_days: 7,
          total_events: 100,
          actions: { deploy: 50, create: 30, update: 20 },
          resource_types: { project: 60, module: 40 },
          users: { admin: 70, ops: 30 },
          statuses: { success: 95, failed: 5 },
          daily_activity: [],
        })
      ),
    );

    render(<AuditLog />);

    await waitFor(() => {
      expect(screen.getByText('Events (7d)')).toBeInTheDocument();
      expect(screen.getByText('100')).toBeInTheDocument();
    });
    expect(screen.getByText('Active Users')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument(); // 2 users
  });
});
