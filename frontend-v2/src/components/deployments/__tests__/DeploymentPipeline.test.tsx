/**
 * Tests for DeploymentPipeline component
 *
 * Covers: empty state when no plan, renders pipeline header with layer/module counts,
 * shows progress when executionStatus provided, renders ReactFlow canvas.
 */
import { useState, useCallback } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { DeploymentPipeline } from '../DeploymentPipeline';
import type { ExecutionPlan, RunProgress, ProjectModule } from '@/types';

// Mock ReactFlow — it requires a DOM with getBoundingClientRect, canvas, etc.
vi.mock('reactflow', () => {
  const MockReactFlow = ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="react-flow">{children}</div>
  );
  const MockControls = () => <div data-testid="react-flow-controls" />;
  const MockBackground = () => <div data-testid="react-flow-background" />;

  const useStateHook = (initial: unknown[]) => {
    const [state, setState] = useState(initial);
    const onChangeHandler = useCallback(() => {}, []);
    return [state, setState, onChangeHandler];
  };

  return {
    __esModule: true,
    default: MockReactFlow,
    Controls: MockControls,
    Background: MockBackground,
    useNodesState: useStateHook,
    useEdgesState: useStateHook,
    BackgroundVariant: { Dots: 'dots' },
    MarkerType: { ArrowClosed: 'arrowclosed' },
    Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
  };
});

// Mock the LogViewerModal since it fetches data on render
vi.mock('@/components/deployments/LogViewerModal', () => ({
  LogViewerModal: () => <div data-testid="log-viewer-modal" />,
}));

const mockModules: ProjectModule[] = [
  {
    id: 1,
    project_id: 1,
    module_library_id: 1,
    module_name: 'vpc',
    path_in_project: 'modules/vpc',
    deployment_order: 0,
    enabled: true,
    status: 'applied',
  },
  {
    id: 2,
    project_id: 1,
    module_library_id: 2,
    module_name: 'eks',
    path_in_project: 'modules/eks',
    deployment_order: 1,
    enabled: true,
    status: 'not_initialized',
    dependencies: [1],
  },
];

const mockExecutionPlan: ExecutionPlan = {
  layers: [
    {
      layer_index: 0,
      modules: [
        { id: 1, name: 'vpc', path: 'modules/vpc', estimated_time_minutes: 3, status: 'pending' },
      ],
      module_count: 1,
    },
    {
      layer_index: 1,
      modules: [
        { id: 2, name: 'eks', path: 'modules/eks', estimated_time_minutes: 5, status: 'pending' },
      ],
      module_count: 1,
    },
  ],
  total_layers: 2,
  total_modules: 2,
  total_estimated_time_sequential: 8,
  total_estimated_time_parallel: 5,
  time_savings_minutes: 3,
  time_savings_percent: 37.5,
  parallelization_factor: 1.6,
};

const mockExecutionStatus: RunProgress = {
  run_handle: 'celery-task-abc123',
  project_id: 1,
  action: 'deploy',
  status: 'in_progress',
  current_layer: 0,
  total_layers: 2,
  layers: [
    {
      layer_index: 0,
      module_ids: [1],
      module_names: ['vpc'],
      status: 'in_progress',
      results: {},
    },
    {
      layer_index: 1,
      module_ids: [2],
      module_names: ['eks'],
      status: 'pending',
      results: {},
    },
  ],
  successful_modules: [],
  failed_modules: [],
  skipped_modules: [],
  started_at: new Date().toISOString(),
  progress_percent: 25,
  completed_at: null,
  duration_seconds: null,
  error_message: null,
};

describe('DeploymentPipeline', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows empty state when no execution plan is provided', () => {
    render(
      <DeploymentPipeline
        executionPlan={undefined}
        executionStatus={undefined}
        modules={mockModules}
      />
    );

    expect(screen.getByText('No execution plan available')).toBeInTheDocument();
    expect(
      screen.getByText('Run Deploy All to see the visual pipeline')
    ).toBeInTheDocument();
  });

  it('renders the pipeline header with layer and module counts', () => {
    render(
      <DeploymentPipeline
        executionPlan={mockExecutionPlan}
        executionStatus={undefined}
        modules={mockModules}
      />
    );

    expect(screen.getByText('Deployment Pipeline')).toBeInTheDocument();
    expect(screen.getByText('2 layers / 2 modules')).toBeInTheDocument();
  });

  it('shows progress percentage when execution is active', () => {
    render(
      <DeploymentPipeline
        executionPlan={mockExecutionPlan}
        executionStatus={mockExecutionStatus}
        modules={mockModules}
      />
    );

    expect(screen.getByText('25% complete')).toBeInTheDocument();
  });

  it('renders the ReactFlow canvas', () => {
    render(
      <DeploymentPipeline
        executionPlan={mockExecutionPlan}
        executionStatus={undefined}
        modules={mockModules}
      />
    );

    expect(screen.getByTestId('react-flow')).toBeInTheDocument();
  });
});
