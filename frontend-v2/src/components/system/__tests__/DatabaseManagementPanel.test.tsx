import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { DatabaseManagementPanel } from '../DatabaseManagementPanel';

beforeEach(() => {
  vi.clearAllMocks();
  // Override with the object shape the component expects (tables as Record)
  server.use(
    http.get('*/api/system/database/stats', () => {
      return HttpResponse.json({
        size_mb: 128,
        table_count: 3,
        total_rows: 15000,
        tables: {
          tasks: { rows: 5000, size_mb: 2.0 },
          projects: { rows: 10, size_mb: 0.03 },
          project_modules: { rows: 25, size_mb: 0.06 },
        },
      });
    })
  );
});

describe('DatabaseManagementPanel', () => {
  it('renders database size and table statistics', async () => {
    render(<DatabaseManagementPanel />);

    await waitFor(() => {
      expect(screen.getByText('Total Database Size')).toBeInTheDocument();
    });
    expect(screen.getByText('128.0 MB')).toBeInTheDocument();
    expect(screen.getByText('Table Statistics')).toBeInTheDocument();
    expect(screen.getByText('5,000 rows')).toBeInTheDocument();
  });

  it('renders cleanup operation buttons', async () => {
    render(<DatabaseManagementPanel />);

    await waitFor(() => {
      expect(screen.getByText('Cleanup Operations')).toBeInTheDocument();
    });
    expect(screen.getByText('Cleanup Deployment Logs')).toBeInTheDocument();
    expect(screen.getByText('Cleanup Audit Logs')).toBeInTheDocument();
    expect(screen.getByText('Cleanup Completed Tasks')).toBeInTheDocument();
    expect(screen.getByText('Optimize Database')).toBeInTheDocument();
  });

  it('shows error state when stats fail to load', async () => {
    server.use(
      http.get('*/api/system/database/stats', () => {
        return HttpResponse.json({ error: 'DB error' }, { status: 500 });
      })
    );

    render(<DatabaseManagementPanel />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load database statistics')).toBeInTheDocument();
    });
  });
});
