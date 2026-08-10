/**
 * Tests for LogViewerModal component
 *
 * Covers: loading state, error state with retry, renders task list and log content,
 * search/filter functionality.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { LogViewerModal } from '../LogViewerModal';

// Polyfill scrollIntoView for jsdom
Element.prototype.scrollIntoView = vi.fn();

// Mock notify to avoid toast side effects
vi.mock('@/lib/notify', () => ({
  notify: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

const defaultProps = {
  open: true,
  onOpenChange: vi.fn(),
  moduleName: 'vpc',
  moduleId: 1,
};

const mockTasksResponse = {
  tasks: [
    {
      id: 1,
      celery_task_id: 'abc-123',
      task_type: 'apply',
      status: 'completed',
      project_id: 1,
      module_id: 1,
      created_at: '2026-02-18T10:00:00Z',
      duration_seconds: 120,
      exit_code: 0,
      logs: 'Apply complete! Resources: 3 added, 0 changed, 0 destroyed.',
      command: 'tofu apply',
    },
    {
      id: 2,
      celery_task_id: 'def-456',
      task_type: 'plan',
      status: 'completed',
      project_id: 1,
      module_id: 1,
      created_at: '2026-02-18T09:00:00Z',
      duration_seconds: 30,
      exit_code: 0,
      logs: 'Plan: 3 to add, 0 to change, 0 to destroy.',
      command: 'tofu plan',
    },
    {
      id: 3,
      celery_task_id: 'ghi-789',
      task_type: 'init',
      status: 'completed',
      project_id: 1,
      module_id: 1,
      created_at: '2026-02-18T08:00:00Z',
      duration_seconds: 15,
      exit_code: 0,
      logs: 'Initializing modules...',
      command: 'tofu init',
    },
  ],
  total: 3,
  limit: 50,
  offset: 0,
};

describe('LogViewerModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state initially then renders tasks', async () => {
    server.use(
      http.get('*/api/tasks', () => {
        return HttpResponse.json(mockTasksResponse);
      }),
      http.get('*/api/tasks/:id', ({ params }) => {
        const task = mockTasksResponse.tasks.find((t) => t.id === Number(params.id));
        return HttpResponse.json(task);
      })
    );

    render(<LogViewerModal {...defaultProps} />);

    // Should show the dialog title even while loading
    await waitFor(() => {
      expect(screen.getByText('Task Logs')).toBeInTheDocument();
    });

    // Should show module info
    await waitFor(() => {
      expect(screen.getByText(/vpc \(Module #1\)/)).toBeInTheDocument();
    });

    // Tasks should load eventually
    await waitFor(() => {
      expect(screen.getByText('3 tasks')).toBeInTheDocument();
    });
  });

  it('shows error state with retry button on API failure', async () => {
    server.use(
      http.get('*/api/tasks', () => {
        return HttpResponse.json(
          { error: 'Internal Server Error' },
          { status: 500 }
        );
      })
    );

    render(<LogViewerModal {...defaultProps} />);

    // Axios throws with "Request failed with status code 500"
    await waitFor(() => {
      expect(screen.getByText(/Request failed with status code 500/)).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('renders task list and shows log content for selected task', async () => {
    server.use(
      http.get('*/api/tasks', () => {
        return HttpResponse.json(mockTasksResponse);
      }),
      http.get('*/api/tasks/:id', ({ params }) => {
        const task = mockTasksResponse.tasks.find((t) => t.id === Number(params.id));
        return HttpResponse.json(task);
      })
    );

    render(<LogViewerModal {...defaultProps} />);

    // Wait for tasks to load and first task to be auto-selected
    await waitFor(() => {
      expect(screen.getByText('3 tasks')).toBeInTheDocument();
    });

    // Task types visible in the sidebar
    await waitFor(() => {
      expect(screen.getByText('apply')).toBeInTheDocument();
      expect(screen.getByText('plan')).toBeInTheDocument();
      expect(screen.getByText('init')).toBeInTheDocument();
    });

    // First task should be auto-selected and its logs shown
    await waitFor(() => {
      expect(
        screen.getByText('Apply complete! Resources: 3 added, 0 changed, 0 destroyed.')
      ).toBeInTheDocument();
    });
  });

  it('does not render when open is false', () => {
    render(<LogViewerModal {...defaultProps} open={false} />);

    expect(screen.queryByText('Task Logs')).not.toBeInTheDocument();
  });

  it('filters tasks by search term', async () => {
    const user = userEvent.setup();

    server.use(
      http.get('*/api/tasks', () => {
        return HttpResponse.json(mockTasksResponse);
      }),
      http.get('*/api/tasks/:id', ({ params }) => {
        const task = mockTasksResponse.tasks.find((t) => t.id === Number(params.id));
        return HttpResponse.json(task);
      })
    );

    render(<LogViewerModal {...defaultProps} />);

    // Wait for tasks to load
    await waitFor(() => {
      expect(screen.getByText('3 tasks')).toBeInTheDocument();
    });

    // Search for "plan" — should filter tasks
    const searchInput = screen.getByLabelText('Search tasks');
    await user.type(searchInput, 'init');

    // After searching, the badge should update to show filtered count
    await waitFor(() => {
      expect(screen.getByText('1 tasks')).toBeInTheDocument();
    });
  });
});
