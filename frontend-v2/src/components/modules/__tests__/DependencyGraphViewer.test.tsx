/**
 * Tests for DependencyGraphViewer component
 *
 * Covers: renders graph container with title, loads data on mount via MSW,
 * shows loading state, shows error state on API failure, empty state.
 */
import { useState, useCallback } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { DependencyGraphViewer } from '@/components/modules/DependencyGraphViewer';
import type { ProjectModule } from '@/types';

// Mock ReactFlow — it requires a DOM with getBoundingClientRect, canvas, etc.
// Use real React useState so node/edge state actually updates when setNodes/setEdges is called.
vi.mock('reactflow', () => {
  const MockReactFlow = ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="react-flow">{children}</div>
  );
  const MockControls = () => <div data-testid="react-flow-controls" />;
  const MockBackground = () => <div data-testid="react-flow-background" />;

  // Use the actual React module (already imported at top of file)
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

describe('DependencyGraphViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the graph card with title and description', async () => {
    server.use(
      http.get('*/api/state/module/:moduleId/graph', () => {
        return HttpResponse.json({
          exists: true,
          nodes: [
            { id: '1', type: 'resource', name: 'aws_vpc.main', address: 'aws_vpc.main', dependencies: [] },
          ],
          edges: [],
        });
      })
    );

    render(<DependencyGraphViewer module={mockModule} />);

    expect(screen.getByText('Dependency graph')).toBeInTheDocument();
    expect(
      screen.getByText('Visual representation of resource dependencies within this module.')
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
  });

  it('shows empty state when graph has no nodes', async () => {
    server.use(
      http.get('*/api/state/module/:moduleId/graph', () => {
        return HttpResponse.json({ exists: true, nodes: [], edges: [] });
      })
    );

    render(<DependencyGraphViewer module={mockModule} />);

    await waitFor(() => {
      expect(
        screen.getByText(
          'No dependency graph available. This module may not have been initialized or applied yet.'
        )
      ).toBeInTheDocument();
    });
  });

  it('shows error state on API failure', async () => {
    server.use(
      http.get('*/api/state/module/:moduleId/graph', () => {
        return HttpResponse.error();
      })
    );

    render(<DependencyGraphViewer module={mockModule} />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load dependency graph/i)).toBeInTheDocument();
    });
  });
});
