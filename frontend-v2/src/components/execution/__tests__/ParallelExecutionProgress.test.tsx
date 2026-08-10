import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { ParallelExecutionProgress } from '../ParallelExecutionProgress';

// Mock the hook to avoid API dependency and give us full control
vi.mock('@/hooks/useParallelExecution', () => ({
  useRunProgress: vi.fn(),
}));

import { useRunProgress } from '@/hooks/useParallelExecution';
const mockHook = vi.mocked(useRunProgress);

const RUN_HANDLE = 'celery-task-abc123';

describe('ParallelExecutionProgress', () => {
  it('shows loading state when data is not available', () => {
    mockHook.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown);

    render(
      <ParallelExecutionProgress projectId={1} runHandle={RUN_HANDLE} action="deploy" />
    );
    expect(screen.getByText('Loading Execution Status...')).toBeInTheDocument();
  });

  it('renders progress when execution is in progress', () => {
    mockHook.mockReturnValue({
      data: {
        run_handle: RUN_HANDLE,
        project_id: 1,
        action: 'deploy',
        status: 'in_progress',
        current_layer: 1,
        total_layers: 3,
        progress_percent: 33,
        successful_modules: [1],
        failed_modules: [],
        skipped_modules: [],
        duration_seconds: 120,
        layers: [],
        started_at: null,
        completed_at: null,
        error_message: null,
      },
      isLoading: false,
    } as unknown);

    render(
      <ParallelExecutionProgress projectId={1} runHandle={RUN_HANDLE} action="deploy" />
    );
    expect(screen.getByText('Parallel Deployment Progress')).toBeInTheDocument();
    expect(screen.getByText('IN_PROGRESS')).toBeInTheDocument();
    expect(screen.getByText('Layer 2 of 3')).toBeInTheDocument();
    expect(screen.getByText('33%')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument(); // successful count
    expect(screen.getByText('2m 0s')).toBeInTheDocument(); // duration
  });

  it('shows completed state with success message', () => {
    mockHook.mockReturnValue({
      data: {
        run_handle: RUN_HANDLE,
        project_id: 1,
        action: 'deploy',
        status: 'completed',
        current_layer: 2,
        total_layers: 3,
        progress_percent: 100,
        successful_modules: [1, 2, 3],
        failed_modules: [],
        skipped_modules: [],
        duration_seconds: 300,
        completed_at: '2026-02-18T10:05:00Z',
        layers: [],
        started_at: null,
        error_message: null,
      },
      isLoading: false,
    } as unknown);

    render(
      <ParallelExecutionProgress projectId={1} runHandle={RUN_HANDLE} action="deploy" />
    );
    expect(screen.getByText('COMPLETED')).toBeInTheDocument();
    expect(screen.getByText(/All modules deployed successfully/)).toBeInTheDocument();
  });

  it('shows failed state with error details', () => {
    mockHook.mockReturnValue({
      data: {
        run_handle: RUN_HANDLE,
        project_id: 1,
        action: 'destroy',
        status: 'failed',
        current_layer: 1,
        total_layers: 3,
        progress_percent: 50,
        successful_modules: [1],
        failed_modules: [2],
        skipped_modules: [],
        error_message: 'Module VPC failed to apply',
        duration_seconds: 60,
        layers: [],
        started_at: null,
        completed_at: null,
      },
      isLoading: false,
    } as unknown);

    render(
      <ParallelExecutionProgress projectId={1} runHandle={RUN_HANDLE} action="destroy" />
    );
    expect(screen.getByText('FAILED')).toBeInTheDocument();
    expect(screen.getByText('Module VPC failed to apply')).toBeInTheDocument();
    expect(screen.getByText('Parallel Destruction Progress')).toBeInTheDocument();
    expect(screen.getByText(/1 module\(s\) failed during destroy/)).toBeInTheDocument();
  });
});
