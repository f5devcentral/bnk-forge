/**
 * Tests for DependencyStatusBadge component and utility functions
 *
 * Covers: renders badge for ready/pending/failed/unknown states,
 * null dependency module, popover content, checkDependenciesReady,
 * getDependencyErrorMessage utility functions.
 */
import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import {
  DependencyStatusBadge,
  checkDependenciesReady,
  getDependencyErrorMessage,
} from '@/components/modules/DependencyStatusBadge';
import type { ProjectModule } from '@/types';

function makeModule(overrides: Partial<ProjectModule> = {}): ProjectModule {
  return {
    id: 1,
    project_id: 100,
    module_library_id: 10,
    module_name: 'vpc',
    path_in_project: 'infra/aws/vpc',
    deployment_order: 1,
    enabled: true,
    status: 'initialized',
    ...overrides,
  };
}

describe('DependencyStatusBadge', () => {
  it('renders "Unknown" badge when dependencyModule is null', () => {
    render(<DependencyStatusBadge dependencyModule={null} />);

    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });

  it('shows popover with "Dependency Not Found" when null module is clicked', async () => {
    const user = userEvent.setup();
    render(<DependencyStatusBadge dependencyModule={null} />);

    await user.click(screen.getByText('Unknown'));

    expect(screen.getByText('Dependency Not Found')).toBeInTheDocument();
    expect(screen.getByText(/no longer exists/i)).toBeInTheDocument();
  });

  it('renders module name with success styling for applied dependency', () => {
    const dep = makeModule({ status: 'applied', module_name: 'vpc-module' });
    render(<DependencyStatusBadge dependencyModule={dep} />);

    expect(screen.getByText('vpc-module')).toBeInTheDocument();
    // The badge should have success-variant styling
    const badge = screen.getByText('vpc-module').closest('[class*="border-success"]');
    expect(badge).toBeInTheDocument();
  });

  it('renders module name with green styling for deployed dependency', () => {
    const dep = makeModule({ status: 'deployed', module_name: 'eks-cluster' });
    render(<DependencyStatusBadge dependencyModule={dep} />);

    expect(screen.getByText('eks-cluster')).toBeInTheDocument();
  });

  it('renders with warning styling for pending (not_initialized) dependency', () => {
    const dep = makeModule({ status: 'not_initialized', module_name: 'rds' });
    render(<DependencyStatusBadge dependencyModule={dep} />);

    expect(screen.getByText('rds')).toBeInTheDocument();
    const badge = screen.getByText('rds').closest('[class*="border-warning"]');
    expect(badge).toBeInTheDocument();
  });

  it('renders with destructive styling for failed dependency', () => {
    const dep = makeModule({ status: 'apply_failed', module_name: 'failed-mod' });
    render(<DependencyStatusBadge dependencyModule={dep} />);

    expect(screen.getByText('failed-mod')).toBeInTheDocument();
    const badge = screen.getByText('failed-mod').closest('[class*="border-destructive"]');
    expect(badge).toBeInTheDocument();
  });

  it('shows popover with status details when badge is clicked', async () => {
    const user = userEvent.setup();
    const dep = makeModule({
      status: 'applied',
      module_name: 'vpc',
      library_module: {
        id: 10,
        name: 'VPC',
        category: 'networking',
        provider: 'aws',
        version: '1.0.0',
        variables: [],
      },
    });
    render(<DependencyStatusBadge dependencyModule={dep} />);

    await user.click(screen.getByText('vpc'));

    expect(screen.getByText('Status: Ready')).toBeInTheDocument();
    expect(screen.getByText(/applied successfully/i)).toBeInTheDocument();
    expect(screen.getByText('Category: networking')).toBeInTheDocument();
  });
});

describe('checkDependenciesReady', () => {
  it('returns ready when module has no dependencies', () => {
    const mod = makeModule({ dependencies: [] });
    const result = checkDependenciesReady(mod, []);

    expect(result.ready).toBe(true);
    expect(result.pendingDeps).toHaveLength(0);
    expect(result.failedDeps).toHaveLength(0);
  });

  it('returns ready when all dependencies are applied', () => {
    const dep1 = makeModule({ id: 2, module_name: 'vpc', status: 'applied' });
    const dep2 = makeModule({ id: 3, module_name: 'rds', status: 'deployed' });
    const mod = makeModule({ dependencies: [2, 3] });

    const result = checkDependenciesReady(mod, [dep1, dep2]);

    expect(result.ready).toBe(true);
  });

  it('returns pending deps when dependencies are not applied', () => {
    const dep = makeModule({ id: 2, module_name: 'vpc', status: 'initialized' });
    const mod = makeModule({ dependencies: [2] });

    const result = checkDependenciesReady(mod, [dep]);

    expect(result.ready).toBe(false);
    expect(result.pendingDeps).toContain('vpc');
  });

  it('returns failed deps when dependencies have failed', () => {
    const dep = makeModule({ id: 2, module_name: 'vpc', status: 'apply_failed' });
    const mod = makeModule({ dependencies: [2] });

    const result = checkDependenciesReady(mod, [dep]);

    expect(result.ready).toBe(false);
    expect(result.failedDeps).toContain('vpc');
  });

  it('returns failed for missing dependency modules', () => {
    const mod = makeModule({ dependencies: [999] });

    const result = checkDependenciesReady(mod, []);

    expect(result.ready).toBe(false);
    expect(result.failedDeps).toContain('Module ID 999');
  });
});

describe('getDependencyErrorMessage', () => {
  it('returns null when all dependencies are ready', () => {
    const dep = makeModule({ id: 2, status: 'applied' });
    const mod = makeModule({ module_name: 'eks', dependencies: [2] });

    const result = getDependencyErrorMessage(mod, [dep]);

    expect(result).toBeNull();
  });

  it('returns error message for failed dependencies', () => {
    const dep = makeModule({ id: 2, module_name: 'vpc', status: 'apply_failed' });
    const mod = makeModule({ module_name: 'eks', dependencies: [2] });

    const result = getDependencyErrorMessage(mod, [dep]);

    expect(result).toContain('Cannot deploy eks');
    expect(result).toContain('vpc');
  });

  it('returns error message for pending dependencies', () => {
    const dep = makeModule({ id: 2, module_name: 'vpc', status: 'not_initialized' });
    const mod = makeModule({ module_name: 'eks', dependencies: [2] });

    const result = getDependencyErrorMessage(mod, [dep]);

    expect(result).toContain('Cannot deploy eks');
    expect(result).toContain('not applied');
  });
});
