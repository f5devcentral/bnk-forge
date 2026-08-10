/**
 * Tests for K8sDetailPanel component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { K8sDetailPanel } from '../K8sDetailPanel';
import type { K8sResource } from '@/types';

beforeEach(() => {
  vi.clearAllMocks();
});

const mockResource: K8sResource = {
  apiVersion: 'v1',
  kind: 'Pod',
  metadata: {
    name: 'nginx-pod-abc123',
    namespace: 'default',
    uid: 'uid-123',
    creationTimestamp: '2026-01-15T10:00:00Z',
    labels: { app: 'nginx', tier: 'frontend' },
  },
  spec: { nodeName: 'worker-1' },
  status: { podIP: '10.0.1.5' },
};

describe('K8sDetailPanel', () => {
  it('renders resource name and kind', () => {
    render(
      <K8sDetailPanel
        resource={mockResource}
        selectedResourceType="pod"
        onClose={vi.fn()}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('nginx-pod-abc123')).toBeInTheDocument();
    expect(screen.getByText(/Pod.*default/)).toBeInTheDocument();
  });

  it('renders action buttons for pods', () => {
    render(
      <K8sDetailPanel
        resource={mockResource}
        selectedResourceType="pod"
        onClose={vi.fn()}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('Describe')).toBeInTheDocument();
    expect(screen.getByText('Logs')).toBeInTheDocument();
    expect(screen.getByText('Terminal')).toBeInTheDocument();
    expect(screen.getByText('Restart')).toBeInTheDocument();
    expect(screen.getByText('Delete')).toBeInTheDocument();
  });

  it('calls onAction when describe is clicked', async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(
      <K8sDetailPanel
        resource={mockResource}
        selectedResourceType="pod"
        onClose={vi.fn()}
        onAction={onAction}
      />
    );

    await user.click(screen.getByText('Describe'));
    expect(onAction).toHaveBeenCalledWith({ type: 'describe', resource: mockResource });
  });

  it('renders labels section', () => {
    render(
      <K8sDetailPanel
        resource={mockResource}
        selectedResourceType="pod"
        onClose={vi.fn()}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('Labels')).toBeInTheDocument();
    expect(screen.getByText('app=nginx')).toBeInTheDocument();
    expect(screen.getByText('tier=frontend')).toBeInTheDocument();
  });

  it('disables node operations action and shows reason for node resources', () => {
    const nodeResource: K8sResource = {
      ...mockResource,
      kind: 'Node',
      metadata: {
        ...mockResource.metadata,
        namespace: undefined,
      },
    };

    render(
      <K8sDetailPanel
        resource={nodeResource}
        selectedResourceType="node"
        nodeOperationsDisabledReason="OpenShift node maintenance should use MachineConfig and operator-managed workflows."
        onClose={vi.fn()}
        onAction={vi.fn()}
      />
    );

    const nodeOpsButton = screen.getByRole('button', { name: 'Node Operations' });
    expect(nodeOpsButton).toBeDisabled();
    expect(screen.getByText(/MachineConfig and operator-managed workflows/)).toBeInTheDocument();
  });

  it('shows ingress mutation-boundary guidance when provided', () => {
    render(
      <K8sDetailPanel
        resource={{ ...mockResource, kind: 'Ingress' }}
        selectedResourceType="ingress"
        networkMutationBoundaryNotice="Forge can mutate a selected Ingress object directly, but OpenShift north-south exposure is commonly Route/operator-managed and remains outside Forge platform mutation scope."
        onClose={vi.fn()}
        onAction={vi.fn()}
      />
    );

    expect(screen.getByText(/selected Ingress object directly/)).toBeInTheDocument();
  });

  it('does not show mutation-boundary guidance for gateway detail without notice', () => {
    render(
      <K8sDetailPanel
        resource={{ ...mockResource, kind: 'Gateway' }}
        selectedResourceType="gateway"
        onClose={vi.fn()}
        onAction={vi.fn()}
      />
    );

    expect(screen.queryByText(/selected Ingress object directly/)).not.toBeInTheDocument();
  });
});
