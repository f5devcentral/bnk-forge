import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ComponentProps } from 'react';
import { render, screen, fireEvent, waitFor } from '@/test/test-utils';
import type { K8sResource } from '@/types';
import { K8sDialogs } from '../K8sDialogs';

const deleteMutateAsync = vi.fn();
const updateMutateAsync = vi.fn();

vi.mock('@/hooks/useK8s', () => ({
  useDeleteK8sResource: () => ({ mutateAsync: deleteMutateAsync }),
  useUpdateK8sResource: () => ({ mutateAsync: updateMutateAsync }),
  useScaleDeployment: () => ({ mutateAsync: vi.fn() }),
  useCreateK8sResource: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock('@/components/k8s/ResourceDeleteDialog', () => ({
  ResourceDeleteDialog: ({ onConfirm }: { onConfirm: () => void }) => <button onClick={onConfirm}>confirm-delete</button>,
}));
vi.mock('@/components/k8s/ResourceEditDialog', () => ({
  ResourceEditDialog: ({ onSubmit }: { onSubmit: (yaml: string, dryRun: boolean) => void }) => (
    <button onClick={() => onSubmit('kind: L4Route', false)}>submit-edit</button>
  ),
}));
vi.mock('@/components/k8s/ResourceDescribeViewer', () => ({
  ResourceDescribeViewer: ({ resourceType }: { resourceType?: string }) => <div>describe:{resourceType}</div>,
}));
vi.mock('@/components/k8s/ResourceEventsViewer', () => ({ ResourceEventsViewer: () => null }));
vi.mock('@/components/k8s/ResourceMetricsViewer', () => ({ ResourceMetricsViewer: () => null }));
vi.mock('@/components/k8s/ResourceCreateDialog', () => ({ ResourceCreateDialog: () => null }));

function resource(kind: string, apiVersion: string): K8sResource {
  return { kind, apiVersion, metadata: { name: 'r1', namespace: 'ns1' } } as K8sResource;
}

type DialogState = ComponentProps<typeof K8sDialogs>['dialogState'];

function renderDialogs(state: Partial<DialogState>) {
  return render(
    <K8sDialogs
      clusterId={1}
      selectedResourceType="l4route"
      selectedNamespace="ns1"
      dialogState={state as DialogState}
      setDialogOpen={vi.fn()}
    />,
  );
}

const l4Route24 = resource('L4Route', 'gateway.k8s.f5.com/v1');

describe('K8sDialogs resource type keys', () => {
  beforeEach(() => {
    deleteMutateAsync.mockReset().mockResolvedValue({});
    updateMutateAsync.mockReset().mockResolvedValue({});
  });

  it('deletes a BNK 2.4 L4Route under its own registry key', async () => {
    renderDialogs({ deleteDialogOpen: true, resourceToDelete: l4Route24 });
    fireEvent.click(await screen.findByText('confirm-delete'));
    await waitFor(() => expect(deleteMutateAsync).toHaveBeenCalled());
    expect(deleteMutateAsync.mock.calls[0][0]).toMatchObject({ resourceType: 'l4route_24', resourceName: 'r1' });
  });

  it('edits a BNK 2.4 L4Route under its own registry key', async () => {
    renderDialogs({ editDialogOpen: true, resourceToEdit: l4Route24 });
    fireEvent.click(await screen.findByText('submit-edit'));
    await waitFor(() => expect(updateMutateAsync).toHaveBeenCalled());
    expect(updateMutateAsync.mock.calls[0][0]).toMatchObject({ resourceType: 'l4route_24' });
  });

  it('describes a BNK 2.4 L4Route under its own registry key', async () => {
    renderDialogs({ describeDialogOpen: true, resourceToDescribe: l4Route24 });
    expect(await screen.findByText('describe:l4route_24')).toBeInTheDocument();
  });

  it('keeps kind.toLowerCase() for core and BNK 2.3 kinds', async () => {
    renderDialogs({ deleteDialogOpen: true, resourceToDelete: resource('Deployment', 'apps/v1') });
    fireEvent.click(await screen.findByText('confirm-delete'));
    await waitFor(() => expect(deleteMutateAsync).toHaveBeenCalled());
    expect(deleteMutateAsync.mock.calls[0][0]).toMatchObject({ resourceType: 'deployment' });

    renderDialogs({ describeDialogOpen: true, resourceToDescribe: resource('L4Route', 'gateway.k8s.f5net.com/v1') });
    expect(await screen.findByText('describe:l4route')).toBeInTheDocument();
  });
});
