/**
 * Tests for SnapshotManager component
 *
 * Covers: renders snapshot list from API, create snapshot button,
 * shows snapshot details (type, created_by, description),
 * empty state, rollback button present.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { SnapshotManager } from '@/components/modules/SnapshotManager';
import type { ProjectModule } from '@/types';

const mockModule: ProjectModule = {
  id: 1,
  project_id: 1,
  module_library_id: 1,
  module_name: 'test-vpc',
  path_in_project: 'modules/vpc',
  deployment_order: 0,
  enabled: true,
  status: 'applied',
  outputs: { vpc_id: 'vpc-12345' },
};

const mockSnapshots = [
  {
    id: 1,
    snapshot_type: 'pre_apply',
    description: 'Snapshot before VPC apply',
    created_by: 'admin',
    terraform_state_serial: 5,
    git_commit: 'abc1234567890',
    module_path: 'modules/vpc',
    created_at: '2026-02-15T10:00:00Z',
    status: 'valid',
  },
  {
    id: 2,
    snapshot_type: 'manual',
    description: 'Manual backup',
    created_by: 'operator',
    module_path: 'modules/vpc',
    created_at: '2026-02-18T08:00:00Z',
    status: 'valid',
  },
];

describe('SnapshotManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders snapshot list with details', async () => {
    server.use(
      http.get('*/api/snapshots/module/:moduleId', () => {
        return HttpResponse.json({ snapshots: mockSnapshots });
      })
    );

    render(<SnapshotManager module={mockModule} />);

    expect(screen.getByText('Snapshots & Rollback')).toBeInTheDocument();
    expect(
      screen.getByText('Create snapshots and rollback to previous states')
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Pre-Apply')).toBeInTheDocument();
    });

    // Check second snapshot type
    expect(screen.getByText('Manual')).toBeInTheDocument();
    // Check descriptions
    expect(screen.getByText('Snapshot before VPC apply')).toBeInTheDocument();
    expect(screen.getByText('Manual backup')).toBeInTheDocument();
    // Check created_by
    expect(screen.getByText('admin')).toBeInTheDocument();
    expect(screen.getByText('operator')).toBeInTheDocument();
    // Check git commit (truncated)
    expect(screen.getByText('abc1234')).toBeInTheDocument();
    // Check state serial
    expect(screen.getByText(/Serial 5/)).toBeInTheDocument();
    // Rollback buttons
    const rollbackButtons = screen.getAllByRole('button', {
      name: /rollback to this snapshot/i,
    });
    expect(rollbackButtons).toHaveLength(2);
  });

  it('renders create snapshot button', async () => {
    server.use(
      http.get('*/api/snapshots/module/:moduleId', () => {
        return HttpResponse.json({ snapshots: [] });
      })
    );

    render(<SnapshotManager module={mockModule} />);

    const createBtn = screen.getByRole('button', { name: /create snapshot/i });
    expect(createBtn).toBeInTheDocument();
  });

  it('shows empty state when no snapshots', async () => {
    server.use(
      http.get('*/api/snapshots/module/:moduleId', () => {
        return HttpResponse.json({ snapshots: [] });
      })
    );

    render(<SnapshotManager module={mockModule} />);

    await waitFor(() => {
      expect(
        screen.getByText(
          /No snapshots found. Snapshots are automatically created before apply\/destroy operations/
        )
      ).toBeInTheDocument();
    });
  });
});
