/**
 * Tests for VariableMappingsManager component
 *
 * Covers: renders mappings table, empty state, add mapping button,
 * mapping details display, apply template button.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { VariableMappingsManager } from '../VariableMappingsManager';

const mockMappings = {
  mappings: [
    {
      id: 1,
      source_variable_name: 'vpc_id',
      target_variable_name: 'network_vpc_id',
      transform_type: 'none',
      is_active: true,
      description: 'Maps VPC ID from network module',
      created_at: '2026-02-01T00:00:00Z',
    },
    {
      id: 2,
      source_variable_name: 'cluster_name',
      target_variable_name: 'eks_cluster_name',
      transform_type: 'lowercase',
      is_active: false,
      description: 'Maps cluster name with lowercase transform',
      created_at: '2026-02-05T00:00:00Z',
    },
  ],
};

describe('VariableMappingsManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    server.use(
      http.get('*/api/project-modules/:moduleId/variable-mappings', () => {
        return HttpResponse.json(mockMappings);
      }),
      http.get('*/api/project-modules/variable-mapping-templates', () => {
        return HttpResponse.json({ templates: [] });
      })
    );
  });

  it('renders title and description', async () => {
    render(<VariableMappingsManager moduleId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Variable Mappings')).toBeInTheDocument();
      expect(screen.getByText(/map external variable names/i)).toBeInTheDocument();
    });
  });

  it('displays mappings in a table', async () => {
    render(<VariableMappingsManager moduleId={1} />);

    await waitFor(() => {
      expect(screen.getByText('vpc_id')).toBeInTheDocument();
      expect(screen.getByText('network_vpc_id')).toBeInTheDocument();
      expect(screen.getByText('cluster_name')).toBeInTheDocument();
      expect(screen.getByText('eks_cluster_name')).toBeInTheDocument();
    });
  });

  it('shows transform type badges', async () => {
    render(<VariableMappingsManager moduleId={1} />);

    await waitFor(() => {
      expect(screen.getByText('None')).toBeInTheDocument();
      expect(screen.getByText('Lowercase')).toBeInTheDocument();
    });
  });

  it('shows active/inactive status badges', async () => {
    render(<VariableMappingsManager moduleId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Active')).toBeInTheDocument();
      expect(screen.getByText('Inactive')).toBeInTheDocument();
    });
  });

  it('shows table headers', async () => {
    render(<VariableMappingsManager moduleId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Source Variable')).toBeInTheDocument();
      expect(screen.getByText('Target Variable')).toBeInTheDocument();
      expect(screen.getByText('Transform')).toBeInTheDocument();
      expect(screen.getByText('Status')).toBeInTheDocument();
      expect(screen.getByText('Actions')).toBeInTheDocument();
    });
  });

  it('shows empty state when no mappings exist', async () => {
    server.use(
      http.get('*/api/project-modules/:moduleId/variable-mappings', () => {
        return HttpResponse.json({ mappings: [] });
      })
    );

    render(<VariableMappingsManager moduleId={1} />);

    await waitFor(() => {
      expect(screen.getByText(/no variable mappings configured/i)).toBeInTheDocument();
    });
  });

  it('has Add Mapping button that opens create dialog', async () => {
    const user = userEvent.setup();
    render(<VariableMappingsManager moduleId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Variable Mappings')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /add mapping/i }));

    await waitFor(() => {
      expect(screen.getByText('Create Mapping')).toBeInTheDocument();
    });
  });

  it('has Apply Template button', async () => {
    render(<VariableMappingsManager moduleId={1} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /apply template/i })).toBeInTheDocument();
    });
  });

  it('shows loading state initially', () => {
    server.use(
      http.get('*/api/project-modules/:moduleId/variable-mappings', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json(mockMappings);
      })
    );

    render(<VariableMappingsManager moduleId={1} />);

    expect(screen.getByText(/loading mappings/i)).toBeInTheDocument();
  });
});
