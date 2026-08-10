/**
 * Tests for ResourceTopologyView component
 *
 * Covers: renders topology card with title, loads categorized resources,
 * shows empty state, displays summary stats, shows categorized groups.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { ResourceTopologyView } from '@/components/modules/ResourceTopologyView';
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

const mockResources = [
  {
    full_address: 'aws_vpc.main',
    type: 'aws_vpc',
    name: 'main',
    provider: 'registry.terraform.io/hashicorp/aws',
    mode: 'managed',
  },
  {
    full_address: 'aws_subnet.public[0]',
    type: 'aws_subnet',
    name: 'public',
    provider: 'registry.terraform.io/hashicorp/aws',
    mode: 'managed',
  },
  {
    full_address: 'aws_route_table.public',
    type: 'aws_route_table',
    name: 'public',
    provider: 'registry.terraform.io/hashicorp/aws',
    mode: 'managed',
  },
  {
    full_address: 'aws_iam_role.lambda',
    type: 'aws_iam_role',
    name: 'lambda',
    provider: 'registry.terraform.io/hashicorp/aws',
    mode: 'managed',
  },
  {
    full_address: 'aws_s3_bucket.logs',
    type: 'aws_s3_bucket',
    name: 'logs',
    provider: 'registry.terraform.io/hashicorp/aws',
    mode: 'managed',
  },
];

describe('ResourceTopologyView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders topology card with title and loads resources', async () => {
    server.use(
      http.get('*/api/state/module/:moduleId/resources', () => {
        return HttpResponse.json({ resources: mockResources });
      })
    );

    render(<ResourceTopologyView module={mockModule} />);

    expect(screen.getByText('Resource topology')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Hierarchical view of infrastructure resources in this module by category.'
      )
    ).toBeInTheDocument();

    // Wait for resources to load and stats to appear
    await waitFor(() => {
      expect(screen.getByText('Total Resources')).toBeInTheDocument();
    });

    // Should show total count
    expect(screen.getByText('5')).toBeInTheDocument();
    // Should show categories count
    expect(screen.getByText('Categories')).toBeInTheDocument();
  });

  it('shows categorized resource groups', async () => {
    server.use(
      http.get('*/api/state/module/:moduleId/resources', () => {
        return HttpResponse.json({ resources: mockResources });
      })
    );

    render(<ResourceTopologyView module={mockModule} />);

    await waitFor(() => {
      // Networking category (vpc, subnet, route_table)
      expect(screen.getByText('Networking')).toBeInTheDocument();
    });

    // Security & IAM category (iam_role)
    expect(screen.getByText('Security & IAM')).toBeInTheDocument();
    // Storage category (s3_bucket)
    expect(screen.getByText('Storage')).toBeInTheDocument();

    // Individual resource names should be visible (groups expanded by default)
    expect(screen.getByText('main')).toBeInTheDocument();
  });

  it('shows empty state when no resources found', async () => {
    server.use(
      http.get('*/api/state/module/:moduleId/resources', () => {
        return HttpResponse.json({ resources: [] });
      })
    );

    render(<ResourceTopologyView module={mockModule} />);

    await waitFor(() => {
      expect(
        screen.getByText('No resources found. This module may not have been applied yet.')
      ).toBeInTheDocument();
    });
  });
});
