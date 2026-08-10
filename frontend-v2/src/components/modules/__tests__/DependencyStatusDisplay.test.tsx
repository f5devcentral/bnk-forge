/**
 * Tests for DependencyStatusDisplay component
 *
 * Covers: renders dependency list with status badges, all-satisfied state,
 * unapplied dependencies warning, deploying state, no-outputs state,
 * returns null when no dependencies.
 */
import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { DependencyStatusDisplay } from '@/components/modules/DependencyStatusDisplay';
import type { ProjectModule } from '@/types';

function makeModule(overrides: Partial<ProjectModule> = {}): ProjectModule {
  return {
    id: 1,
    project_id: 100,
    module_library_id: 10,
    module_name: 'eks',
    path_in_project: 'infra/aws/eks',
    deployment_order: 2,
    enabled: true,
    status: 'initialized',
    ...overrides,
  };
}

function makeDep(overrides: Partial<ProjectModule> = {}): ProjectModule {
  return {
    id: 2,
    project_id: 100,
    module_library_id: 11,
    module_name: 'vpc',
    path_in_project: 'infra/aws/vpc',
    deployment_order: 1,
    enabled: true,
    status: 'applied',
    library_module: {
      id: 11,
      name: 'AWS VPC',
      category: 'networking',
      provider: 'aws',
      version: '1.0.0',
      variables: [],
    },
    outputs: { vpc_id: 'vpc-123', subnet_ids: ['subnet-a', 'subnet-b'] },
    ...overrides,
  };
}

describe('DependencyStatusDisplay', () => {
  it('returns null when module has no dependencies', () => {
    const mod = makeModule({ dependencies: [] });
    const { container } = render(<DependencyStatusDisplay module={mod} allModules={[]} />);

    expect(container.innerHTML).toBe('');
  });

  it('returns null when dependencies is undefined', () => {
    const mod = makeModule({ dependencies: undefined });
    const { container } = render(<DependencyStatusDisplay module={mod} allModules={[]} />);

    expect(container.innerHTML).toBe('');
  });

  it('renders dependency count badge and heading', () => {
    const dep = makeDep();
    const mod = makeModule({ dependencies: [2] });
    render(<DependencyStatusDisplay module={mod} allModules={[dep]} />);

    expect(screen.getByText('Dependencies')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  it('shows all-satisfied message when all deps are applied with outputs', () => {
    const dep = makeDep({ status: 'applied', outputs: { vpc_id: 'vpc-123' } });
    const mod = makeModule({ dependencies: [2] });
    render(<DependencyStatusDisplay module={mod} allModules={[dep]} />);

    expect(screen.getByText(/all dependencies are satisfied/i)).toBeInTheDocument();
  });

  it('shows "Ready" badge for applied dependency with outputs', () => {
    const dep = makeDep({ status: 'applied', outputs: { vpc_id: 'vpc-123' } });
    const mod = makeModule({ dependencies: [2] });
    render(<DependencyStatusDisplay module={mod} allModules={[dep]} />);

    expect(screen.getByText('Ready')).toBeInTheDocument();
    expect(screen.getByText('1 outputs')).toBeInTheDocument();
  });

  it('shows "Not deployed" badge for unapplied dependency', () => {
    const dep = makeDep({ status: 'initialized', outputs: undefined });
    const mod = makeModule({ dependencies: [2] });
    render(<DependencyStatusDisplay module={mod} allModules={[dep]} />);

    expect(screen.getByText('Not deployed')).toBeInTheDocument();
    expect(screen.getByText(/deploy the following modules first/i)).toBeInTheDocument();
  });

  it('shows "Deploying..." badge for in-progress dependency', () => {
    const dep = makeDep({ status: 'applying', outputs: undefined });
    const mod = makeModule({ dependencies: [2] });
    render(<DependencyStatusDisplay module={mod} allModules={[dep]} />);

    expect(screen.getByText('Deploying...')).toBeInTheDocument();
  });

  it('shows "No outputs" badge for applied dependency without outputs', () => {
    const dep = makeDep({ status: 'applied', outputs: {} });
    const mod = makeModule({ dependencies: [2] });
    render(<DependencyStatusDisplay module={mod} allModules={[dep]} />);

    expect(screen.getByText('No outputs')).toBeInTheDocument();
  });

  it('renders dependency module name and path', () => {
    const dep = makeDep();
    const mod = makeModule({ dependencies: [2] });
    render(<DependencyStatusDisplay module={mod} allModules={[dep]} />);

    expect(screen.getByText('AWS VPC')).toBeInTheDocument();
    expect(screen.getByText('infra/aws/vpc')).toBeInTheDocument();
  });

  it('renders multiple dependencies', () => {
    const dep1 = makeDep({ id: 2, module_name: 'vpc', status: 'applied', outputs: { vpc_id: 'x' } });
    const dep2 = makeDep({
      id: 3,
      module_name: 'rds',
      path_in_project: 'infra/aws/rds',
      status: 'initialized',
      outputs: undefined,
      library_module: {
        id: 12,
        name: 'AWS RDS',
        category: 'database',
        provider: 'aws',
        version: '1.0.0',
        variables: [],
      },
    });
    const mod = makeModule({ dependencies: [2, 3] });
    render(<DependencyStatusDisplay module={mod} allModules={[dep1, dep2]} />);

    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('AWS VPC')).toBeInTheDocument();
    expect(screen.getByText('AWS RDS')).toBeInTheDocument();
  });
});
