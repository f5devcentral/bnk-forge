import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { K8sResourceTable } from '../K8sResourceTable';
import type { K8sResource } from '@/types';

const nodeResource: K8sResource = {
  kind: 'Node',
  apiVersion: 'v1',
  name: 'worker-a',
  metadata: {
    name: 'worker-a',
    uid: 'node-worker-a',
    creationTimestamp: '2026-03-01T00:00:00Z',
    labels: {
      'kubernetes.io/hostname': 'worker-a',
      'node.kubernetes.io/instance-type': 'm5.large',
    },
  },
  status: {
    addresses: [{ type: 'InternalIP', address: '10.0.0.10' }],
    conditions: [{ type: 'Ready', status: 'True' }],
  },
};

const ingressResource: K8sResource = {
  kind: 'Ingress',
  apiVersion: 'networking.k8s.io/v1',
  name: 'edge-ingress',
  metadata: {
    name: 'edge-ingress',
    namespace: 'default',
    uid: 'ingress-edge',
    creationTimestamp: '2026-03-01T00:00:00Z',
  },
};

describe('K8sResourceTable', () => {
  it('disables node operations with reason when provided', async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();

    render(
      <K8sResourceTable
        resources={[nodeResource]}
        selectedResourceType="node"
        selectedResource={null}
        detailedView={false}
        nodeOperationsDisabledReason="Node day-2 changes are operator-managed on this cluster."
        onSelectResource={vi.fn()}
        onAction={onAction}
        borderDefault="border-zinc-200"
      />,
    );

    await user.click(screen.getByRole('button', { name: /resource actions/i }));

    const nodeOpsItem = screen.getByText('Node Operations (OpenShift-managed)');
    expect(nodeOpsItem).toBeInTheDocument();
    expect(nodeOpsItem.closest('[role="menuitem"]')).toHaveAttribute('data-disabled');
    expect(screen.getByText(/Node day-2 changes are operator-managed/)).toBeInTheDocument();
    expect(onAction).not.toHaveBeenCalled();
  });

  it('renders ingress mutation-boundary notice in actions menu when provided', async () => {
    const user = userEvent.setup();

    render(
      <K8sResourceTable
        resources={[ingressResource]}
        selectedResourceType="ingress"
        selectedResource={null}
        detailedView={false}
        networkMutationBoundaryNotice="Forge can mutate a selected Ingress object directly, but OpenShift north-south exposure is commonly Route/operator-managed and remains outside Forge platform mutation scope."
        onSelectResource={vi.fn()}
        onAction={vi.fn()}
        borderDefault="border-zinc-200"
      />,
    );

    await user.click(screen.getByRole('button', { name: /resource actions/i }));

    expect(screen.getByText(/selected Ingress object directly/)).toBeInTheDocument();
  });

  it('does not render mutation-boundary notice for gateway row when no notice is provided', async () => {
    const user = userEvent.setup();

    render(
      <K8sResourceTable
        resources={[{ ...ingressResource, kind: 'Gateway', metadata: { ...ingressResource.metadata, uid: 'gateway-edge' } }]}
        selectedResourceType="gateway"
        selectedResource={null}
        detailedView={false}
        onSelectResource={vi.fn()}
        onAction={vi.fn()}
        borderDefault="border-zinc-200"
      />,
    );

    await user.click(screen.getByRole('button', { name: /resource actions/i }));

    expect(screen.queryByText(/selected Ingress object directly/)).not.toBeInTheDocument();
  });
});
