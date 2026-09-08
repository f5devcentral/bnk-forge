import { describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@/test/test-utils';
import { MultiHostDeployModal } from '../BareMetalPanel';
import type { BareMetalHost } from '@/types';

const mockHosts: (BareMetalHost & { dpu_info?: Record<string, unknown>[] })[] = [
  {
    id: 101,
    name: 'host-01.lab',
    ip_address: '10.10.10.1',
    ssh_port: 22,
    status: 'online',
    topology: 'multi-host',
    dpus: [
      {
        id: 1,
        name: 'dpu-01',
        serial_number: 'SN1234',
        pci_address: '0000:03:00.0',
        mac_address: '00:11:22:33:44:55',
        status: 'ready',
      },
    ],
    dpu_info: [{ pci_address: '0000:03:00.0', model: 'BlueField-2' }],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 102,
    name: 'host-02.lab',
    ip_address: '10.10.10.2',
    ssh_port: 22,
    status: 'online',
    topology: 'multi-host',
    dpus: [
      {
        id: 2,
        name: 'dpu-02',
        serial_number: 'SN5678',
        pci_address: '0000:04:00.0',
        mac_address: '00:11:22:33:44:56',
        status: 'ready',
      },
    ],
    dpu_info: [{ pci_address: '0000:04:00.0', model: 'BlueField-2' }],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

describe('MultiHostDeployModal', () => {
  it('renders modal with host list and DPU selections', () => {
    render(
      <MultiHostDeployModal
        isOpen={true}
        onClose={vi.fn()}
        hosts={mockHosts}
        onStartDeployment={vi.fn()}
        onPreviewInBlueprintDialog={vi.fn()}
      />
    );

    expect(screen.getByText('Deploy Multi-Host Cluster (F5 BNK)')).toBeInTheDocument();
    expect(screen.getByText('1. Control Plane Host *')).toBeInTheDocument();
    expect(screen.getByText('Blueprint Stack Engine')).toBeInTheDocument();
  });

  it('triggers Preview & Configure Blueprint with correct multi-host payload', async () => {
    const user = userEvent.setup();
    const handleStartDeployment = vi.fn().mockResolvedValue(undefined);
    const handlePreviewInBlueprintDialog = vi.fn();

    render(
      <MultiHostDeployModal
        isOpen={true}
        onClose={vi.fn()}
        hosts={mockHosts}
        onStartDeployment={handleStartDeployment}
        onPreviewInBlueprintDialog={handlePreviewInBlueprintDialog}
      />
    );

    // Select Blueprint Stack Engine
    const blueprintBtn = screen.getByRole('button', { name: /Blueprint Stack Engine/i });
    await user.click(blueprintBtn);

    // Click "Preview & Configure Blueprint"
    const previewBtn = screen.getByRole('button', { name: /Preview & Configure Blueprint/i });
    expect(previewBtn).toBeInTheDocument();
    await user.click(previewBtn);

    await waitFor(() => {
      expect(handlePreviewInBlueprintDialog).toHaveBeenCalledTimes(1);
    });

    const [slug, variables] = handlePreviewInBlueprintDialog.mock.calls[0];
    expect(slug).toBeDefined();
    expect(variables.control_plane_host_id).toBe(101);
    expect(variables.topology).toBe('multi-host');
  });

  it('records single-DPU host selections in dpu_selections (gap #5)', async () => {
    // Both mock hosts have exactly ONE DPU (the common case). Previously the
    // single-DPU branch rendered a static span and never recorded the
    // selection, dropping these hosts from dpu_selections. The seeding effect
    // must populate them for every checked DPU host.
    const user = userEvent.setup();
    const handleStartDeployment = vi.fn().mockResolvedValue(undefined);

    render(
      <MultiHostDeployModal
        isOpen={true}
        onClose={vi.fn()}
        hosts={mockHosts}
        onStartDeployment={handleStartDeployment}
        onPreviewInBlueprintDialog={vi.fn()}
      />
    );

    const submitBtn = screen.getByRole('button', { name: /Save Draft & View in Blueprints/i });
    await user.click(submitBtn);

    await waitFor(() => {
      expect(handleStartDeployment).toHaveBeenCalledTimes(1);
    });

    const payload = handleStartDeployment.mock.calls[0][0];
    expect(payload.blueprint_dpu_selections[101]).toBe('0000:03:00.0');
    expect(payload.blueprint_dpu_selections[102]).toBe('0000:04:00.0');
  });
});
