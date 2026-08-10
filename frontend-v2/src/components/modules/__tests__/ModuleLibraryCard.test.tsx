/**
 * Tests for ModuleLibraryCard component
 *
 * Covers: renders module name/version/description, category and provider badges,
 * variable count, Add to Project button callback, documentation link.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ModuleLibraryCard } from '@/components/modules/ModuleLibraryCard';
import type { ModuleLibrary } from '@/types';

function makeLibraryModule(overrides: Partial<ModuleLibrary> = {}): ModuleLibrary {
  return {
    id: 1,
    name: 'AWS VPC',
    description: 'Creates a VPC with subnets',
    category: 'networking',
    provider: 'aws',
    version: '2.1.0',
    variables: [],
    ...overrides,
  };
}

describe('ModuleLibraryCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders module name, version, and description', () => {
    const mod = makeLibraryModule();
    render(<ModuleLibraryCard module={mod} />);

    expect(screen.getByText('AWS VPC')).toBeInTheDocument();
    expect(screen.getByText('v2.1.0')).toBeInTheDocument();
    expect(screen.getByText('Creates a VPC with subnets')).toBeInTheDocument();
  });

  it('renders category and provider badges', () => {
    const mod = makeLibraryModule();
    render(<ModuleLibraryCard module={mod} />);

    expect(screen.getByText('networking')).toBeInTheDocument();
    expect(screen.getByText('aws')).toBeInTheDocument();
  });

  it('shows variable count when variables exist', () => {
    const mod = makeLibraryModule({
      variables: [
        { name: 'cidr', type: 'string', required: true, sensitive: false },
        { name: 'region', type: 'string', required: false, sensitive: false },
        { name: 'tags', type: 'map', required: false, sensitive: false },
      ],
    });
    render(<ModuleLibraryCard module={mod} />);

    expect(screen.getByText('3 variables')).toBeInTheDocument();
  });

  it('shows singular "variable" for single variable', () => {
    const mod = makeLibraryModule({
      variables: [
        { name: 'cidr', type: 'string', required: true, sensitive: false },
      ],
    });
    render(<ModuleLibraryCard module={mod} />);

    expect(screen.getByText('1 variable')).toBeInTheDocument();
  });

  it('does not show variable count when no variables', () => {
    const mod = makeLibraryModule({ variables: [] });
    render(<ModuleLibraryCard module={mod} />);

    expect(screen.queryByText(/variable/)).not.toBeInTheDocument();
  });

  it('renders Add to Project button and calls onAdd callback', async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    const mod = makeLibraryModule();
    render(<ModuleLibraryCard module={mod} onAdd={onAdd} />);

    const addButton = screen.getByRole('button', { name: /add to project/i });
    expect(addButton).toBeInTheDocument();

    await user.click(addButton);

    expect(onAdd).toHaveBeenCalledWith(mod);
  });

  it('does not render Add button when onAdd is not provided', () => {
    const mod = makeLibraryModule();
    render(<ModuleLibraryCard module={mod} />);

    expect(screen.queryByRole('button', { name: /add to project/i })).not.toBeInTheDocument();
  });

  it('does not render description when not provided', () => {
    const mod = makeLibraryModule({ description: undefined });
    render(<ModuleLibraryCard module={mod} />);

    expect(screen.queryByText('Creates a VPC with subnets')).not.toBeInTheDocument();
  });

  it('renders canonical declared compatibility and required capabilities when present', () => {
    const mod = makeLibraryModule({
      platform_compatibility: {
        supported_profiles: ['eks', 'generic_onprem'],
        required_capabilities: ['gateway_api'],
        compatibility_scope: 'declared_subset',
        declared_any: false,
        unmapped_supported_platforms: [],
      },
    });

    render(<ModuleLibraryCard module={mod} />);

    expect(screen.getByText('Declared compatibility: EKS, Generic On-Prem')).toBeInTheDocument();
    expect(screen.getByText('Required capabilities: gateway_api')).toBeInTheDocument();
  });

  it('surfaces OCP runtime support semantics when compatibility includes OpenShift profile', () => {
    const mod = makeLibraryModule({
      platform_compatibility: {
        supported_profiles: ['ocp'],
        required_capabilities: ['scc', 'operator_framework'],
        compatibility_scope: 'declared_subset',
        declared_any: false,
        unmapped_supported_platforms: [],
      },
    });

    render(<ModuleLibraryCard module={mod} />);

    expect(screen.getByText(/OpenShift\/OKD support semantics:/i)).toBeInTheDocument();
    expect(screen.getByText(/Security Context Constraints/i)).toBeInTheDocument();
    expect(screen.getByText(/operator-managed platform workflow semantics/i)).toBeInTheDocument();
  });

  it('uses conservative all-profiles wording for declared any compatibility', () => {
    const mod = makeLibraryModule({
      platform_compatibility: {
        supported_profiles: ['generic_onprem', 'eks', 'aks', 'gke', 'ocp'],
        required_capabilities: [],
        compatibility_scope: 'declared_all_profiles',
        declared_any: true,
        unmapped_supported_platforms: [],
      },
    });

    render(<ModuleLibraryCard module={mod} />);

    expect(screen.getByText('Declared compatibility: all current platform profiles')).toBeInTheDocument();
  });

  it('surfaces unmapped declarations conservatively when canonical mapping is unavailable', () => {
    const mod = makeLibraryModule({
      platform_compatibility: {
        supported_profiles: [],
        required_capabilities: [],
        compatibility_scope: 'unspecified',
        declared_any: false,
        unmapped_supported_platforms: ['k3s'],
      },
    });

    render(<ModuleLibraryCard module={mod} />);

    expect(screen.getByText('Declared compatibility: k3s (unmapped profile label)')).toBeInTheDocument();
  });

  it('includes unmapped label note alongside canonical profiles', () => {
    const mod = makeLibraryModule({
      platform_compatibility: {
        supported_profiles: ['eks'],
        required_capabilities: [],
        compatibility_scope: 'declared_subset',
        declared_any: false,
        unmapped_supported_platforms: ['k3s'],
      },
    });

    render(<ModuleLibraryCard module={mod} />);

    expect(
      screen.getByText('Declared compatibility: EKS (+ k3s declared as unmapped profile label)')
    ).toBeInTheDocument();
  });
});
