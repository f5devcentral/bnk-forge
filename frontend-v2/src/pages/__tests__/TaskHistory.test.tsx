/**
 * Tests for TaskHistory page
 *
 * Covers: page title, search input, task table rows, sidebar status filters,
 * 7-day summary stats, loading state, empty state, task type columns.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import TaskHistory from '@/pages/TaskHistory';
import {
  useTasks,
  useTaskStats,
  useCancelTask,
  useDeleteTask,
  useArchiveTask,
  useBulkDeleteTasks,
  useBulkArchiveTasks,
  useCleanupOldTasks,
} from '@/hooks/useTasks';
import type { Task } from '@/types';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@/hooks/useTasks', () => ({
  useTasks: vi.fn(),
  useTask: vi.fn(() => ({ data: null })),
  useCancelTask: vi.fn(),
  useTaskStats: vi.fn(),
  useDeleteTask: vi.fn(),
  useArchiveTask: vi.fn(),
  useBulkDeleteTasks: vi.fn(),
  useBulkArchiveTasks: vi.fn(),
  useCleanupOldTasks: vi.fn(),
}));

vi.mock('@/hooks/useProjects', () => ({
  useProjects: vi.fn(() => ({ data: [{ id: 1, name: 'test-project' }] })),
}));

const mockTasks: Task[] = [
  {
    id: 101,
    task_type: 'plan',
    status: 'completed',
    project_name: 'web-app',
    module_name: 'vpc-network',
    duration_seconds: 45,
    created_at: '2025-01-15T10:00:00Z',
  } as unknown as Task,
  {
    id: 102,
    task_type: 'apply',
    status: 'in_progress',
    project_name: 'web-app',
    module_name: 'bnk-gateway',
    duration_seconds: undefined,
    created_at: '2025-01-15T10:05:00Z',
  } as unknown as Task,
  {
    id: 103,
    task_type: 'init',
    status: 'failed',
    project_name: 'api-service',
    module_name: null,
    duration_seconds: 12,
    created_at: '2025-01-15T09:50:00Z',
  } as unknown as Task,
];

function setupDefaultMocks(overrides: {
  tasks?: Task[];
  isLoading?: boolean;
} = {}) {
  vi.mocked(useTasks).mockReturnValue({
    data: { tasks: overrides.tasks !== undefined ? overrides.tasks : mockTasks, total: (overrides.tasks ?? mockTasks).length },
    isLoading: overrides.isLoading ?? false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useTasks>);

  vi.mocked(useTaskStats).mockReturnValue({
    data: {
      total_tasks: 150,
      by_status: { completed: 120, failed: 15, in_progress: 10, queued: 5 },
    },
  } as unknown as ReturnType<typeof useTaskStats>);

  vi.mocked(useCancelTask).mockReturnValue({
    mutate: vi.fn(),
  } as unknown as ReturnType<typeof useCancelTask>);

  const deleteMutate = vi.fn();
  const archiveMutate = vi.fn();
  const bulkDeleteMutate = vi.fn();
  const bulkArchiveMutate = vi.fn();
  const cleanupMutate = vi.fn();
  vi.mocked(useDeleteTask).mockReturnValue(
    { mutate: deleteMutate, isPending: false } as unknown as ReturnType<typeof useDeleteTask>,
  );
  vi.mocked(useArchiveTask).mockReturnValue(
    { mutate: archiveMutate, isPending: false } as unknown as ReturnType<typeof useArchiveTask>,
  );
  vi.mocked(useBulkDeleteTasks).mockReturnValue(
    { mutate: bulkDeleteMutate, isPending: false } as unknown as ReturnType<typeof useBulkDeleteTasks>,
  );
  vi.mocked(useBulkArchiveTasks).mockReturnValue(
    { mutate: bulkArchiveMutate, isPending: false } as unknown as ReturnType<typeof useBulkArchiveTasks>,
  );
  vi.mocked(useCleanupOldTasks).mockReturnValue(
    { mutate: cleanupMutate, isPending: false } as unknown as ReturnType<typeof useCleanupOldTasks>,
  );

  return { deleteMutate, archiveMutate, bulkDeleteMutate, bulkArchiveMutate, cleanupMutate };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('TaskHistory', () => {
  it('renders page title "Operations Log"', () => {
    setupDefaultMocks();
    render(<TaskHistory />);
    expect(screen.getByText('Operations Log')).toBeInTheDocument();
  });

  it('renders search input with correct placeholder', () => {
    setupDefaultMocks();
    render(<TaskHistory />);
    expect(screen.getByPlaceholderText(/search operations/i)).toBeInTheDocument();
  });

  it('renders task rows in the table', () => {
    setupDefaultMocks();
    render(<TaskHistory />);
    expect(screen.getByText('#101')).toBeInTheDocument();
    expect(screen.getByText('#102')).toBeInTheDocument();
    expect(screen.getByText('#103')).toBeInTheDocument();
  });

  it('renders status chip filters and 7-day KPI strip', () => {
    // D-020: sidebar replaced by a status chip-filter row + a top KPI strip.
    // "All" chip is always present; KPI tile labelled "Total (7d)" anchors
    // the 7-day summary stats.
    setupDefaultMocks();
    render(<TaskHistory />);
    expect(screen.getByRole('button', { name: /^All\b/ })).toBeInTheDocument();
    expect(screen.getByText(/Total \(7d\)/i)).toBeInTheDocument();
  });

  it('renders loading skeletons while data is fetching', () => {
    setupDefaultMocks({ tasks: [], isLoading: true });
    render(<TaskHistory />);
    const skeletons = document.querySelectorAll('[class*="animate-pulse"], [class*="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('renders empty state when no operations exist', () => {
    setupDefaultMocks({ tasks: [] });
    render(<TaskHistory />);
    expect(screen.getByText(/no operations/i)).toBeInTheDocument();
  });

  it('filters operations by search query', async () => {
    setupDefaultMocks();
    const user = userEvent.setup();
    render(<TaskHistory />);
    const searchInput = screen.getByPlaceholderText(/search operations/i);
    await user.type(searchInput, 'nonexistent-xyz');
    // After typing a non-matching query, task rows should be filtered out
    expect(screen.queryByText('#101')).not.toBeInTheDocument();
  });

  it('renders the standard Refresh button', () => {
    setupDefaultMocks();
    render(<TaskHistory />);
    // D-020: the page now uses the shared PageHeader refresh (icon-only,
    // accessible via its aria-label) instead of a bespoke text button.
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument();
  });

  // --- #21: operations-log delete/archive/cleanup controls ---

  it('renders the "Show archived" toggle and "Clean up old" button', () => {
    setupDefaultMocks();
    render(<TaskHistory />);
    expect(screen.getByRole('button', { name: /show archived/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /clean up old/i })).toBeInTheDocument();
  });

  it('selecting a row reveals the bulk bar and Archive bulk-archives the selection', async () => {
    const { bulkArchiveMutate } = setupDefaultMocks();
    const user = userEvent.setup();
    render(<TaskHistory />);

    await user.click(screen.getByLabelText('Select operation #101'));
    expect(screen.getByText('1 selected')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Archive' }));
    expect(bulkArchiveMutate).toHaveBeenCalledWith({ taskIds: [101], archived: true });
  });

  it('bulk Delete requires typing DELETE before it bulk-deletes', async () => {
    const { bulkDeleteMutate } = setupDefaultMocks();
    const user = userEvent.setup();
    render(<TaskHistory />);

    await user.click(screen.getByLabelText('Select operation #101'));
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    const confirmInput = screen.getByPlaceholderText('Type "DELETE" here');
    await user.type(confirmInput, 'DELETE');
    await user.click(screen.getByRole('button', { name: /delete permanently/i }));

    expect(bulkDeleteMutate).toHaveBeenCalledWith([101]);
  });
});
