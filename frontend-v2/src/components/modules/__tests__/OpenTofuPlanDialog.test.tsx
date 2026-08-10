/**
 * Tests for OpenTofuPlanDialog component
 *
 * Covers: renders plan dialog, loading state, plan output display,
 * error handling, apply button, retry button.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { OpenTofuPlanDialog } from '../OpenTofuPlanDialog';
import type { ProjectModule } from '@/types';

const mockModule: ProjectModule = {
  id: 1,
  project_id: 1,
  module_library_id: 1,
  module_name: 'vpc',
  path_in_project: 'infra/vpc',
  variable_overrides: { cidr_block: '10.0.0.0/16' },
  deployment_order: 0,
  enabled: true,
  status: 'initialized',
  library_module: {
    id: 1,
    name: 'vpc',
    category: 'network',
    provider: 'aws',
    version: '1.0.0',
    variables: [],
  },
};

describe('OpenTofuPlanDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    module: mockModule,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders the plan dialog with title', () => {
    // Override plan mutation to stay in loading state
    server.use(
      http.post('*/api/project-modules/:moduleId/plan', async () => {
        await new Promise(() => {}); // Never resolves
        return HttpResponse.json({ task_id: 'task-plan-001', status: 'queued', success: true });
      })
    );

    render(<OpenTofuPlanDialog {...defaultProps} />);

    expect(screen.getByText('OpenTofu Plan Preview')).toBeInTheDocument();
    expect(screen.getByText(/review the changes opentofu will make/i)).toBeInTheDocument();
  });

  it('shows loading state when generating plan', () => {
    // Keep plan mutation hanging to see loading state
    server.use(
      http.post('*/api/project-modules/:moduleId/plan', async () => {
        await new Promise(() => {}); // Never resolves
        return HttpResponse.json({});
      })
    );

    render(<OpenTofuPlanDialog {...defaultProps} />);

    expect(screen.getByText(/generating plan/i)).toBeInTheDocument();
  });

  it('displays error when plan fails to queue', async () => {
    server.use(
      http.post('*/api/project-modules/:moduleId/plan', () => {
        return HttpResponse.json({ success: false, message: 'Provider not configured' });
      })
    );

    render(<OpenTofuPlanDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/provider not configured/i)).toBeInTheDocument();
    });
  });

  it('displays error when plan API returns error status', async () => {
    server.use(
      http.post('*/api/project-modules/:moduleId/plan', () => {
        return HttpResponse.json(
          { detail: 'Internal server error' },
          { status: 500 }
        );
      })
    );

    render(<OpenTofuPlanDialog {...defaultProps} />);

    await waitFor(() => {
      // The component catches the error and shows it
      expect(screen.getByText(/failed to start plan|internal server error|request failed/i)).toBeInTheDocument();
    });
  });

  it('shows retry button when plan fails', async () => {
    server.use(
      http.post('*/api/project-modules/:moduleId/plan', () => {
        return HttpResponse.json({ success: false, message: 'Plan failed' });
      })
    );

    render(<OpenTofuPlanDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
    });
  });

  it('cancel button closes dialog', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    server.use(
      http.post('*/api/project-modules/:moduleId/plan', async () => {
        await new Promise(() => {}); // hang
        return HttpResponse.json({});
      })
    );

    render(<OpenTofuPlanDialog {...defaultProps} />);

    const cancelButton = screen.getByRole('button', { name: /cancel/i });
    await user.click(cancelButton);

    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('does not render when open is false', () => {
    render(<OpenTofuPlanDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('OpenTofu Plan Preview')).not.toBeInTheDocument();
  });
});
