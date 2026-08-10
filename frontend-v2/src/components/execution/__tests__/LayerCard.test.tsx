import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { LayerCard } from '../LayerCard';
import type { ExecutionLayer } from '@/types';

describe('LayerCard', () => {
  it('renders layer with modules list', () => {
    const layer: ExecutionLayer = {
      layer_index: 1,
      module_count: 2,
      can_run_parallel: true,
      estimated_time_minutes: 5,
      modules: [
        { id: 1, name: 'vpc', path: 'modules/vpc', estimated_time_minutes: 3, status: 'applied' },
        { id: 2, name: 'eks', path: 'modules/eks', estimated_time_minutes: 5, status: 'pending' },
      ],
    };

    render(<LayerCard layer={layer} />);
    expect(screen.getByText('Layer 1')).toBeInTheDocument();
    expect(screen.getByText('vpc')).toBeInTheDocument();
    expect(screen.getByText('eks')).toBeInTheDocument();
    expect(screen.getByText('2 parallel')).toBeInTheDocument();
    expect(screen.getByText('5 min')).toBeInTheDocument();
  });

  it('renders layer with module_names format and shows status badges', () => {
    const layer: ExecutionLayer = {
      layer_index: 0,
      module_count: 2,
      module_names: ['vpc', 'subnet'],
      module_ids: [1, 2],
      results: {
        1: { success: true },
        2: { success: false, error: 'timeout' },
      },
      status: 'completed',
      estimated_time_minutes: 3,
    };

    render(<LayerCard layer={layer} showStatus />);
    expect(screen.getByText('Layer 0')).toBeInTheDocument();
    expect(screen.getByText('vpc')).toBeInTheDocument();
    expect(screen.getByText('subnet')).toBeInTheDocument();
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('shows empty state when no modules', () => {
    const layer: ExecutionLayer = {
      layer_index: 2,
      module_count: 0,
      estimated_time_minutes: 0,
    };

    render(<LayerCard layer={layer} />);
    expect(screen.getByText('No modules in this layer')).toBeInTheDocument();
  });
});
