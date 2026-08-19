import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { DriftCheckHistory } from '../DriftCheckHistory';

const mockChecks = [
  {
    id: 1,
    project_id: 1,
    module_id: 10,
    module_name: 'vpc-module',
    status: 'completed',
    drift_detected: true,
    drift_summary: 'Security group rules changed',
    drift_details: {
      resource_changes: { add: 0, change: 1, destroy: 0 },
      changed_resources: [{ address: 'aws_security_group.main', action: 'update' }],
    },
    created_at: '2026-02-18T09:00:00Z',
    updated_at: '2026-02-18T09:00:00Z',
  },
  {
    id: 2,
    project_id: 1,
    module_id: 11,
    module_name: 'eks-cluster',
    status: 'completed',
    drift_detected: false,
    created_at: '2026-02-17T09:00:00Z',
    updated_at: '2026-02-17T09:00:00Z',
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  server.use(
    http.get('*/api/projects/:projectId/drift/checks', () => {
      return HttpResponse.json(mockChecks);
    })
  );
});

describe('DriftCheckHistory', () => {
  it('renders drift check history with module names and statuses', async () => {
    render(<DriftCheckHistory projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Drift Check History')).toBeInTheDocument();
    });
    expect(screen.getByText('vpc-module')).toBeInTheDocument();
    expect(screen.getByText('Drift Detected')).toBeInTheDocument();
    expect(screen.getByText('eks-cluster')).toBeInTheDocument();
    expect(screen.getByText('No Drift')).toBeInTheDocument();
  });

  it('shows resource change badges for drifted checks', async () => {
    render(<DriftCheckHistory projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('~1')).toBeInTheDocument();
    });
  });

  it('opens the drifted check and offers a working Reconcile action (#70)', async () => {
    // DriftDetailPanel carried the Reconcile button but was mounted nowhere;
    // the details dialog hand-rolled a read-only subset. Clicking a drifted
    // row must now surface Reconcile, and Reconcile must apply THAT module.
    let applied: { moduleId: string; body: unknown } | null = null;
    server.use(
      http.post('*/api/project-modules/:moduleId/apply', async ({ request, params }) => {
        applied = { moduleId: String(params.moduleId), body: await request.json() };
        return HttpResponse.json({ task_id: 77, message: 'queued' });
      }),
      http.get('*/api/tasks/:id', () => HttpResponse.json({ id: 77, status: 'queued', task_type: 'apply' })),
    );
    const user = userEvent.setup();
    render(<DriftCheckHistory projectId={1} />);

    await user.click(await screen.findByText('vpc-module'));

    const reconcile = await screen.findByRole('button', { name: /reconcile/i });
    expect(reconcile).toBeEnabled();
    await user.click(reconcile);

    await waitFor(() => {
      expect(applied).not.toBeNull();
    });
    expect(applied!.moduleId).toBe('10');            // the drifted check's module, not another
    expect(applied!.body).toEqual({ auto_approve: true });
  });

  it('still shows a failed check\'s error in the dialog', async () => {
    // The panel does not render error_message; the dialog keeps that alert.
    server.use(
      http.get('*/api/projects/:projectId/drift/checks', () =>
        HttpResponse.json([{ ...mockChecks[0], id: 3, status: 'failed', drift_detected: false,
                             error_message: 'tofu plan exited 1: provider auth expired' }])),
    );
    const user = userEvent.setup();
    render(<DriftCheckHistory projectId={1} />);
    await user.click(await screen.findByText('vpc-module'));
    expect(await screen.findByText(/provider auth expired/)).toBeInTheDocument();

    // Regression: a failed check has drift_detected:false, but "No Drift" would
    // be a lie -- tofu plan never completed, so nothing is known. The panel must
    // report the status, not the boolean. Before the status-aware badge, the
    // dialog showed a red error alert and a green "No Drift" beside it.
    expect(screen.queryByText('No Drift')).not.toBeInTheDocument();
    // And the panel's own header must say Failed (there are now two "Failed"
    // badges: the row and the panel), never zero.
    expect(screen.getAllByText('Failed').length).toBeGreaterThanOrEqual(2);
  });

  it('shows empty state when no checks exist', async () => {
    server.use(
      http.get('*/api/projects/:projectId/drift/checks', () => {
        return HttpResponse.json([]);
      })
    );

    render(<DriftCheckHistory projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('No drift checks yet')).toBeInTheDocument();
    });
  });
});
