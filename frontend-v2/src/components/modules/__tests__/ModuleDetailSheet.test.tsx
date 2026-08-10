/**
 * Tests for ModuleDetailSheet component
 *
 * Covers: renders module details, shows variables, dependencies section,
 * outputs section, Add to Project button, update available banner.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ModuleDetailSheet } from '../ModuleDetailSheet';
import type { ModuleLibrary } from '@/types';

const mockModule: ModuleLibrary = {
  id: 1,
  name: 'vpc',
  description: 'AWS VPC module for creating Virtual Private Clouds',
  category: 'network',
  provider: 'aws',
  version: '1.0.0',
  variables: [
    { name: 'cidr_block', type: 'string', description: 'VPC CIDR block', required: true, sensitive: false },
    { name: 'enable_dns', type: 'bool', description: 'Enable DNS support', required: false, sensitive: false },
    { name: 'tags', type: 'map', description: 'Resource tags', required: false, sensitive: false },
  ],
  readme: 'This is the VPC module README with detailed documentation about how to use the module and configure it properly.\n\nIt supports multiple availability zones, NAT gateways, and advanced routing configurations.\n\nSee the examples directory for usage patterns and best practices for production deployments.',
  documentation_url: 'https://docs.example.com/vpc',
  deployment_order: 0,
  platform_compatibility: {
    supported_profiles: ['eks', 'ocp'],
    required_capabilities: ['gateway_api'],
    compatibility_scope: 'declared_subset',
    declared_any: false,
    unmapped_supported_platforms: [],
  },
  dependencies_metadata: {
    required: [
      { module: 'infra/aws/iam', reason: 'Requires IAM roles for VPC flow logs' },
    ],
    optional: [
      { module: 'infra/aws/cloudwatch', reason: 'For VPC flow log delivery' },
    ],
  },
  outputs_metadata: [
    { name: 'vpc_id', type: 'string', description: 'The ID of the VPC' },
    { name: 'subnet_ids', type: 'list', description: 'List of subnet IDs', used_by: ['eks', 'rds'] },
  ],
  engine_type: 'ansible',
  lifecycle_capabilities: {
    supports_init: true,
    supports_plan: true,
    supports_apply: true,
    supports_destroy: false,
    supports_refresh: false,
    supports_drift: false,
  },
};

describe('ModuleDetailSheet', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    module: mockModule,
    onAddToProject: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders module name and version', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('vpc')).toBeInTheDocument();
    expect(screen.getByText(/version 1.0.0/i)).toBeInTheDocument();
  });

  it('shows category and provider badges', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('network')).toBeInTheDocument();
    // Provider badge appears in quick info
    const awsBadges = screen.getAllByText('aws');
    expect(awsBadges.length).toBeGreaterThanOrEqual(1);
  });

  it('shows engine and lifecycle capability details truthfully', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('Module Engine & Lifecycle')).toBeInTheDocument();
    expect(screen.getByText('Engine: Ansible')).toBeInTheDocument();
    expect(screen.getByText('Supports Init')).toBeInTheDocument();
    expect(screen.getByText('Supports Plan')).toBeInTheDocument();
    expect(screen.queryByText('Supports Destroy')).not.toBeInTheDocument();
  });

  it('displays module description', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('Description')).toBeInTheDocument();
    expect(screen.getByText(/aws vpc module for creating virtual private clouds/i)).toBeInTheDocument();
  });

  it('renders declared platform compatibility section with conservative wording', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('Declared Platform Compatibility')).toBeInTheDocument();
    expect(screen.getByText('Declared compatibility: EKS, OpenShift/OKD')).toBeInTheDocument();
    expect(screen.getByText('Required capabilities: gateway_api')).toBeInTheDocument();
    expect(screen.getByText(/OpenShift\/OKD support semantics:/i)).toBeInTheDocument();
    expect(screen.getByText(/runtime support follows detected cluster context/i)).toBeInTheDocument();
    expect(screen.getByText(/does not guarantee runtime support/i)).toBeInTheDocument();
  });

  it('surfaces unmapped declared compatibility labels when profiles are unavailable', () => {
    const unmappedCompatibilityModule = {
      ...mockModule,
      platform_compatibility: {
        supported_profiles: [],
        required_capabilities: [],
        compatibility_scope: 'unspecified' as const,
        declared_any: false,
        unmapped_supported_platforms: ['k3s'],
      },
    };

    render(<ModuleDetailSheet {...defaultProps} module={unmappedCompatibilityModule} />);

    expect(screen.getByText('Declared compatibility: k3s (unmapped profile label)')).toBeInTheDocument();
  });

  it('shows variable count badge', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('3 variables')).toBeInTheDocument();
  });

  it('lists required variables', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('Required Variables')).toBeInTheDocument();
    expect(screen.getByText('cidr_block')).toBeInTheDocument();
    expect(screen.getByText('enable_dns')).toBeInTheDocument();
  });

  it('shows variable types', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    // "string" appears multiple times (variable types + output types)
    const stringBadges = screen.getAllByText('string');
    expect(stringBadges.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('bool')).toBeInTheDocument();
    expect(screen.getByText('map')).toBeInTheDocument();
  });

  it('displays dependencies section with required dependencies', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('Dependencies')).toBeInTheDocument();
    expect(screen.getByText('IAM')).toBeInTheDocument(); // Extracted from path
    expect(screen.getByText(/requires iam roles/i)).toBeInTheDocument();
  });

  it('shows optional dependencies', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('Optional Dependencies')).toBeInTheDocument();
    expect(screen.getByText('CLOUDWATCH')).toBeInTheDocument();
  });

  it('shows "no external dependencies" when none exist', () => {
    const noDepsModule = { ...mockModule, dependencies_metadata: undefined };
    render(<ModuleDetailSheet {...defaultProps} module={noDepsModule} />);

    expect(screen.getByText(/no external dependencies/i)).toBeInTheDocument();
  });

  it('displays outputs section', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('Outputs')).toBeInTheDocument();
    expect(screen.getByText('vpc_id')).toBeInTheDocument();
    expect(screen.getByText('subnet_ids')).toBeInTheDocument();
  });

  it('shows "used by" for outputs', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText(/used by: eks, rds/i)).toBeInTheDocument();
  });

  it('shows deployment order when set', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('Deployment Order')).toBeInTheDocument();
    expect(screen.getByText(/deployed in order 0/i)).toBeInTheDocument();
  });

  it('displays README/documentation section', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('Documentation')).toBeInTheDocument();
  });

  it('has "Show More" button for long documentation', () => {
    // The readme is > 200 chars so Show More should appear
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByRole('button', { name: /show more/i })).toBeInTheDocument();
  });

  it('has external documentation link', () => {
    render(<ModuleDetailSheet {...defaultProps} />);

    expect(screen.getByText('View Full Documentation')).toBeInTheDocument();
  });

  it('Add to Project button calls onAddToProject', async () => {
    const user = userEvent.setup();
    render(<ModuleDetailSheet {...defaultProps} />);

    await user.click(screen.getByRole('button', { name: /add to project/i }));

    expect(defaultProps.onAddToProject).toHaveBeenCalledWith(mockModule);
    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('shows update available banner when update_available is true', () => {
    const moduleWithUpdate = {
      ...mockModule,
      update_available: true,
      latest_version: '2.0.0',
    };
    render(<ModuleDetailSheet {...defaultProps} module={moduleWithUpdate} />);

    expect(screen.getByText('Update Available')).toBeInTheDocument();
    expect(screen.getByText(/version 2.0.0 is available/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /upgrade/i })).toBeInTheDocument();
  });

  it('does not render when module is null', () => {
    render(<ModuleDetailSheet {...defaultProps} module={null} />);

    expect(screen.queryByText('vpc')).not.toBeInTheDocument();
  });
});
