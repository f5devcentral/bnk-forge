import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { NodeOperationsDialog } from '../NodeOperationsDialog';

describe('NodeOperationsDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    clusterId: 1,
    nodeName: 'worker-node-1',
    nodeStatus: { spec: { unschedulable: false } },
  };

  it('renders dialog title with node name', () => {
    render(<NodeOperationsDialog {...defaultProps} />);
    expect(screen.getByText('Node Operations: worker-node-1')).toBeInTheDocument();
  });

  it('shows Cordon button when node is schedulable', () => {
    render(<NodeOperationsDialog {...defaultProps} />);
    expect(screen.getByText('Schedulable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Cordon Node/ })).toBeInTheDocument();
  });

  it('shows Uncordon button when node is unschedulable', () => {
    const unschedulable = {
      ...defaultProps,
      nodeStatus: { spec: { unschedulable: true } },
    };
    render(<NodeOperationsDialog {...unschedulable} />);
    expect(screen.getByText('Unschedulable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Uncordon Node/ })).toBeInTheDocument();
  });
});
