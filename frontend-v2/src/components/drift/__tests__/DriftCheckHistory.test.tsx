import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
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
