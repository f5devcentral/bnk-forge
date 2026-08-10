import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { F5BNKResourceTable } from '../F5BNKResourceTable';
import type { K8sResource } from '@/types';

const mockResources: K8sResource[] = [
  {
    name: 'my-vlan',
    kind: 'F5BigCneVlan',
    apiVersion: 'f5.io/v1',
    metadata: {
      name: 'my-vlan',
      namespace: 'bnk-system',
      uid: 'uid-1',
      creationTimestamp: '2026-01-15T00:00:00Z',
    },
    status: {
      conditions: [{ type: 'Ready', status: 'True', reason: 'OK' }],
    },
  },
  {
    name: 'my-fw-policy',
    kind: 'F5BigFwPolicy',
    apiVersion: 'f5.io/v1',
    metadata: {
      name: 'my-fw-policy',
      namespace: 'bnk-system',
      uid: 'uid-2',
      creationTimestamp: '2026-02-01T00:00:00Z',
    },
    status: {
      conditions: [{ type: 'Ready', status: 'False', reason: 'NotReady' }],
    },
  },
];

const defaultProps = {
  resources: mockResources,
  selectedResource: null,
  onSelectResource: vi.fn(),
  onDescribe: vi.fn(),
  onEdit: vi.fn(),
  onDelete: vi.fn(),
  onNavigateView: vi.fn(),
  onOpenDialog: vi.fn(),
  borderDefault: 'border-slate-200',
};

describe('F5BNKResourceTable', () => {
  it('renders table with resource names, namespaces, and statuses', () => {
    render(<F5BNKResourceTable {...defaultProps} />);
    expect(screen.getByText('my-vlan')).toBeInTheDocument();
    expect(screen.getByText('my-fw-policy')).toBeInTheDocument();
    expect(screen.getAllByText('bnk-system')).toHaveLength(2);
    expect(screen.getByText('Ready')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('renders column headers', () => {
    render(<F5BNKResourceTable {...defaultProps} />);
    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('Namespace')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Age')).toBeInTheDocument();
    expect(screen.getByText('Actions')).toBeInTheDocument();
  });

  it('calls onSelectResource when a row is clicked', async () => {
    const user = userEvent.setup();
    render(<F5BNKResourceTable {...defaultProps} />);

    await user.click(screen.getByText('my-vlan'));
    expect(defaultProps.onSelectResource).toHaveBeenCalledWith(mockResources[0]);
  });
});
