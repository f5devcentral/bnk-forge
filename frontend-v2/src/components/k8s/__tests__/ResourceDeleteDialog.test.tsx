import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { ResourceDeleteDialog } from '../ResourceDeleteDialog';

const mockResource = {
  kind: 'Deployment',
  apiVersion: 'apps/v1',
  metadata: { name: 'nginx', namespace: 'default', uid: '123' },
  spec: {},
  status: {},
};

describe('ResourceDeleteDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    resource: mockResource,
    onConfirm: vi.fn(),
    isLoading: false,
  };

  it('renders delete confirmation with resource name and kind', () => {
    render(<ResourceDeleteDialog {...defaultProps} />);
    expect(screen.getByText('Delete Deployment?')).toBeInTheDocument();
    expect(screen.getByText('nginx')).toBeInTheDocument();
  });

  it('shows destructive warning for Namespace resources', () => {
    const nsResource = { ...mockResource, kind: 'Namespace' };
    render(<ResourceDeleteDialog {...defaultProps} resource={nsResource} />);
    expect(screen.getByText(/data loss/)).toBeInTheDocument();
    expect(screen.getByText(/All resources in this namespace/)).toBeInTheDocument();
  });

  it('returns null when resource is null', () => {
    const { container } = render(<ResourceDeleteDialog {...defaultProps} resource={null} />);
    expect(container.innerHTML).toBe('');
  });
});
