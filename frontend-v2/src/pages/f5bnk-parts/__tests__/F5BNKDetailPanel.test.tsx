import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { F5BNKDetailPanel } from '../F5BNKDetailPanel';
import type { K8sResource } from '@/types';

const mockResource: K8sResource = {
  name: 'my-vlan',
  kind: 'F5SPKVlan',
  apiVersion: 'f5.io/v1',
  metadata: {
    name: 'my-vlan',
    namespace: 'bnk-system',
    uid: 'uid-1',
    creationTimestamp: '2026-01-15T00:00:00Z',
  },
  spec: { tag: 100 },
  status: {},
};

const defaultProps = {
  resource: mockResource,
  onClose: vi.fn(),
  onDescribe: vi.fn(),
  onEdit: vi.fn(),
  onDelete: vi.fn(),
  onNavigateView: vi.fn(),
  onOpenDialog: vi.fn(),
  borderDefault: 'border-slate-200',
};

describe('F5BNKDetailPanel', () => {
  it('renders resource name and kind in the header', () => {
    render(<F5BNKDetailPanel {...defaultProps} />);
    expect(screen.getByText('my-vlan')).toBeInTheDocument();
    expect(screen.getByText(/F5SPKVlan/)).toBeInTheDocument();
  });

  it('renders Describe, Edit YAML, and Delete buttons', () => {
    render(<F5BNKDetailPanel {...defaultProps} />);
    expect(screen.getByText('Describe')).toBeInTheDocument();
    expect(screen.getByText('Edit YAML')).toBeInTheDocument();
    expect(screen.getByText('Delete')).toBeInTheDocument();
  });

  it('renders the resource-specific detail component (VlanDetail)', () => {
    render(<F5BNKDetailPanel {...defaultProps} />);
    // VlanDetail renders "VLAN Configuration" section
    expect(screen.getByText('VLAN Configuration')).toBeInTheDocument();
  });

  it('renders fallback for unknown resource kinds', () => {
    const unknownResource: K8sResource = {
      name: 'mystery',
      kind: 'UnknownKind',
      apiVersion: 'custom/v1',
      metadata: { name: 'mystery', namespace: 'default', uid: 'uid-99' },
    };
    render(<F5BNKDetailPanel {...defaultProps} resource={unknownResource} />);
    expect(screen.getByText('mystery')).toBeInTheDocument();
    // Fallback shows metadata with Kind field
    expect(screen.getByText('UnknownKind')).toBeInTheDocument();
  });
});
