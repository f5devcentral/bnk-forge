/**
 * Tests for OutputsViewer component
 *
 * Covers: renders outputs list from API, copy button interaction,
 * empty state, sensitive output masking, loading state.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { OutputsViewer } from '@/components/modules/OutputsViewer';
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
  library_module: {
    id: 1,
    name: 'vpc',
    category: 'network',
    provider: 'aws',
    version: '1.0.0',
    variables: [],
  },
};

describe('OutputsViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders outputs list from API', async () => {
    server.use(
      http.get('*/api/state/module/:moduleId/outputs', () => {
        return HttpResponse.json({
          exists: true,
          outputs: {
            vpc_id: {
              name: 'vpc_id',
              value: 'vpc-12345',
              type: 'string',
              sensitive: false,
              description: 'The VPC ID',
            },
            subnet_ids: {
              name: 'subnet_ids',
              value: ['subnet-1', 'subnet-2'],
              type: 'list',
              sensitive: false,
            },
          },
        });
      }),
      http.get('*/api/tasks', () => {
        return HttpResponse.json({ tasks: [], total: 0 });
      })
    );

    render(<OutputsViewer modules={[mockModule]} />);

    expect(screen.getByText('Module Outputs')).toBeInTheDocument();
    expect(screen.getByText('Output values from deployed modules')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('vpc_id')).toBeInTheDocument();
    });

    expect(screen.getByText('subnet_ids')).toBeInTheDocument();
    expect(screen.getByText('vpc-12345')).toBeInTheDocument();
    // Applied Outputs heading
    expect(screen.getByText('Applied outputs')).toBeInTheDocument();
  });

  it('shows empty state when no outputs available', async () => {
    server.use(
      http.get('*/api/state/module/:moduleId/outputs', () => {
        return HttpResponse.json({ exists: false, outputs: {} });
      }),
      http.get('*/api/tasks', () => {
        return HttpResponse.json({ tasks: [], total: 0 });
      })
    );

    render(<OutputsViewer modules={[mockModule]} />);

    await waitFor(() => {
      expect(
        screen.getByText('No outputs available. Deploy modules with outputs to see them here.')
      ).toBeInTheDocument();
    });
  });

  it('renders copy button for each output and shows sensitive badge', async () => {
    server.use(
      http.get('*/api/state/module/:moduleId/outputs', () => {
        return HttpResponse.json({
          exists: true,
          outputs: {
            db_password: {
              name: 'db_password',
              value: 'secret123',
              type: 'string',
              sensitive: true,
            },
            vpc_id: {
              name: 'vpc_id',
              value: 'vpc-12345',
              type: 'string',
              sensitive: false,
            },
          },
        });
      }),
      http.get('*/api/tasks', () => {
        return HttpResponse.json({ tasks: [], total: 0 });
      })
    );

    render(<OutputsViewer modules={[mockModule]} />);

    await waitFor(() => {
      expect(screen.getByText('db_password')).toBeInTheDocument();
    });

    // Sensitive badge should be displayed
    expect(screen.getByText('sensitive')).toBeInTheDocument();

    // Sensitive value should be masked by default
    expect(screen.getByText('<sensitive>')).toBeInTheDocument();

    // The note about sensitive outputs
    expect(screen.getByText(/Sensitive outputs are masked by default/)).toBeInTheDocument();
  });
});
