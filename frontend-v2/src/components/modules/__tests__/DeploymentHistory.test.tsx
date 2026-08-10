/**
 * Tests for DeploymentHistory component
 *
 * Covers: renders deployment list from API, shows empty state,
 * renders filter buttons, displays deployment details.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { DeploymentHistory } from '@/components/modules/DeploymentHistory';
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

const mockDeployments = [
  {
    id: 1,
    action: 'apply',
    status: 'success',
    triggered_by: 'admin',
    started_at: '2026-02-18T10:00:00Z',
    completed_at: '2026-02-18T10:05:00Z',
    duration_seconds: 300,
    resources_to_add: 3,
    resources_to_change: 1,
    resources_to_destroy: 0,
    exit_code: 0,
    environment: 'dev',
    git_commit: 'abc1234567890',
    git_branch: 'main',
  },
  {
    id: 2,
    action: 'plan',
    status: 'success',
    triggered_by: 'ci',
    started_at: '2026-02-18T09:00:00Z',
    completed_at: '2026-02-18T09:01:00Z',
    duration_seconds: 60,
    resources_to_add: 0,
    resources_to_change: 0,
    resources_to_destroy: 0,
    exit_code: 0,
  },
];

describe('DeploymentHistory', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders deployment list from API', async () => {
    server.use(
      http.get('*/api/project-modules/:moduleId/deployments', () => {
        return HttpResponse.json({ deployments: mockDeployments });
      })
    );

    render(<DeploymentHistory module={mockModule} />);

    expect(screen.getByText('Deployment history')).toBeInTheDocument();
    expect(screen.getByText('Timeline of all deployment operations.')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('apply')).toBeInTheDocument();
    });

    // Check resource change counts are displayed
    expect(screen.getByText('+3')).toBeInTheDocument();
    expect(screen.getByText('~1')).toBeInTheDocument();
    // Check triggered_by is displayed
    expect(screen.getByText('admin')).toBeInTheDocument();
    // Check git info
    expect(screen.getByText(/main@abc1234/)).toBeInTheDocument();
  });

  it('shows empty state when no deployments', async () => {
    server.use(
      http.get('*/api/project-modules/:moduleId/deployments', () => {
        return HttpResponse.json({ deployments: [] });
      })
    );

    render(<DeploymentHistory module={mockModule} />);

    await waitFor(() => {
      expect(
        screen.getByText(
          'No deployment history found. Run plan, apply, or destroy to see deployments here.'
        )
      ).toBeInTheDocument();
    });
  });

  it('renders filter buttons and refresh button', () => {
    server.use(
      http.get('*/api/project-modules/:moduleId/deployments', () => {
        return HttpResponse.json({ deployments: [] });
      })
    );

    render(<DeploymentHistory module={mockModule} />);

    // Action filter button
    expect(screen.getByText(/Action: All/)).toBeInTheDocument();
    // Status filter button
    expect(screen.getByText(/Status: All/)).toBeInTheDocument();
    // Refresh button
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
  });
});
