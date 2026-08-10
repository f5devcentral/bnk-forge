import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';

import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { NodeDiscoveryPanel } from '@/components/discovery/NodeDiscoveryPanel';
import { notify } from '@/lib/notify';

function createDiscoveryJobResponse(projectId: number, status: 'pending' | 'in_progress' | 'completed' | 'failed', overrides?: Record<string, unknown>) {
  return {
    id: 555,
    project_id: projectId,
    ssh_credential_id: null,
    has_shared_credentials: true,
    status,
    total_nodes: 2,
    completed_nodes: status === 'completed' ? 2 : 0,
    failed_nodes: status === 'failed' ? 2 : 0,
    celery_task_id: null,
    error_message: status === 'failed' ? 'All nodes failed discovery' : null,
    started_at: '2026-04-08T11:00:00Z',
    completed_at: status === 'pending' || status === 'in_progress' ? null : '2026-04-08T11:00:10Z',
    created_at: '2026-04-08T11:00:00Z',
    created_by: 'admin',
    nodes: [],
    ...overrides,
  };
}

describe('NodeDiscoveryPanel', () => {
  beforeAll(() => {
    if (!HTMLElement.prototype.hasPointerCapture) {
      HTMLElement.prototype.hasPointerCapture = () => false;
    }
    if (!HTMLElement.prototype.setPointerCapture) {
      HTMLElement.prototype.setPointerCapture = () => {};
    }
    if (!HTMLElement.prototype.releasePointerCapture) {
      HTMLElement.prototype.releasePointerCapture = () => {};
    }
    if (!HTMLElement.prototype.scrollIntoView) {
      HTMLElement.prototype.scrollIntoView = () => {};
    }
  });

  it('renders recent jobs and loads selected job details', async () => {
    let listCapturedProjectId: string | null = null;
    let capturedProjectId: string | null = null;
    let capturedJobId: string | null = null;

    server.use(
      http.get('*/api/projects/:projectId/discovery', ({ params }) => {
        listCapturedProjectId = params.projectId as string;
        return HttpResponse.json({
          jobs: [
            {
              id: 42,
              project_id: Number(params.projectId),
              ssh_credential_id: null,
              has_shared_credentials: true,
              status: 'completed',
              total_nodes: 2,
              completed_nodes: 1,
              failed_nodes: 1,
              celery_task_id: null,
              error_message: '1 of 2 nodes failed',
              started_at: '2026-04-08T09:00:00Z',
              completed_at: '2026-04-08T09:00:12Z',
              created_at: '2026-04-08T09:00:00Z',
              created_by: 'admin',
              nodes: [],
            },
          ],
        });
      }),
      http.get('*/api/projects/:projectId/discovery/:jobId', ({ params }) => {
        capturedProjectId = params.projectId as string;
        capturedJobId = params.jobId as string;

        return HttpResponse.json({
          id: 42,
          project_id: Number(params.projectId),
          ssh_credential_id: null,
          has_shared_credentials: true,
          status: 'completed',
          total_nodes: 2,
          completed_nodes: 1,
          failed_nodes: 1,
          celery_task_id: null,
          error_message: '1 of 2 nodes failed',
          started_at: '2026-04-08T09:00:00Z',
          completed_at: '2026-04-08T09:00:12Z',
          created_at: '2026-04-08T09:00:00Z',
          created_by: 'admin',
          nodes: [
            {
              id: 900,
              discovery_job_id: 42,
              ip_address: '10.1.1.10',
              hostname: 'node-a.lab',
              ssh_credential_id: 5,
              has_ssh_credentials: true,
              status: 'completed',
              error_message: null,
              os_type: 'ubuntu',
              os_version: '24.04',
              os_pretty_name: 'Ubuntu 24.04 LTS',
              kernel_version: '6.8.0',
              architecture: 'x86_64',
              is_dpu_host: true,
              is_dpu_node: false,
              dpu_count: 1,
              dpu_details: [{ pci_address: '0000:00:00.0', description: 'BlueField-3 DPU' }],
              k8s_installed: true,
              k8s_running: true,
              k8s_version: 'v1.30.1',
              k8s_distribution: 'unknown',
              k8s_role: 'unknown',
              container_runtime: 'containerd',
              network_interfaces: [{ name: 'eth0', state: 'UP', mac: 'aa:bb:cc:dd:ee:ff' }],
              discovered_at: '2026-04-08T09:00:10Z',
              created_at: '2026-04-08T09:00:00Z',
            },
            {
              id: 901,
              discovery_job_id: 42,
              ip_address: '10.1.1.11',
              hostname: null,
              ssh_credential_id: null,
              has_ssh_credentials: false,
              status: 'failed',
              error_message: 'SSH host unreachable or connection refused',
              os_type: null,
              os_version: null,
              os_pretty_name: null,
              kernel_version: null,
              architecture: null,
              is_dpu_host: false,
              is_dpu_node: false,
              dpu_count: 0,
              dpu_details: null,
              k8s_installed: false,
              k8s_running: false,
              k8s_version: null,
              k8s_distribution: null,
              k8s_role: null,
              container_runtime: null,
              network_interfaces: null,
              discovered_at: null,
              created_at: '2026-04-08T09:00:00Z',
            },
          ],
        });
      }),
    );

    const user = userEvent.setup();
    render(<NodeDiscoveryPanel projectId={77} canTrigger={false} />);

    await waitFor(() => {
      expect(screen.getByText('Job #42')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Job #42'));

    await waitFor(() => {
      expect(screen.getByText('Discovery Job #42')).toBeInTheDocument();
    });

    expect(listCapturedProjectId).toBe('77');
    expect(capturedProjectId).toBe('77');
    expect(capturedJobId).toBe('42');

    expect(screen.getByText('1 of 2 nodes failed')).toBeInTheDocument();
    expect(screen.getByText('10.1.1.10')).toBeInTheDocument();
    expect(screen.getByText('10.1.1.11')).toBeInTheDocument();
    expect(screen.getByText('DPUs Found')).toBeInTheDocument();
    expect(screen.getByText('K8s Running')).toBeInTheDocument();
  });

  it('shows friendly error when selected job cannot be loaded in project scope', async () => {
    server.use(
      http.get('*/api/projects/:projectId/discovery', ({ params }) => {
        return HttpResponse.json({
          jobs: [
            {
              id: 999,
              project_id: Number(params.projectId),
              ssh_credential_id: null,
              has_shared_credentials: true,
              status: 'failed',
              total_nodes: 1,
              completed_nodes: 0,
              failed_nodes: 1,
              celery_task_id: null,
              error_message: 'All nodes failed discovery',
              started_at: '2026-04-08T09:00:00Z',
              completed_at: '2026-04-08T09:00:05Z',
              created_at: '2026-04-08T09:00:00Z',
              created_by: 'admin',
              nodes: [],
            },
          ],
        });
      }),
      http.get('*/api/projects/:projectId/discovery/:jobId', () => {
        return HttpResponse.json({ error: { code: 'NOT_FOUND', message: 'discovery job not found' } }, { status: 404 });
      }),
    );

    const user = userEvent.setup();
    render(<NodeDiscoveryPanel projectId={5} canTrigger={false} />);

    await waitFor(() => {
      expect(screen.getByText('Job #999')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Job #999'));

    await waitFor(() => {
      expect(screen.getByText(/Failed to load discovery job #999/i)).toBeInTheDocument();
    });
  });

  it('prevents duplicate trigger while another job is active', async () => {
    server.use(
      http.get('*/api/ssh-credentials', () => HttpResponse.json([])),
      http.get('*/api/projects/:projectId/discovery', ({ params }) => {
        return HttpResponse.json({
          jobs: [
            {
              id: 101,
              project_id: Number(params.projectId),
              ssh_credential_id: null,
              has_shared_credentials: true,
              status: 'in_progress',
              total_nodes: 2,
              completed_nodes: 1,
              failed_nodes: 0,
              celery_task_id: null,
              error_message: null,
              started_at: '2026-04-08T10:00:00Z',
              completed_at: null,
              created_at: '2026-04-08T10:00:00Z',
              created_by: 'admin',
              nodes: [],
            },
          ],
        });
      }),
    );

    render(<NodeDiscoveryPanel projectId={8} />);

    await waitFor(() => {
      expect(screen.getByText('Job #101')).toBeInTheDocument();
    });

    const triggerButton = screen.getByRole('button', { name: /run discovery/i });
    expect(triggerButton).toBeDisabled();
    expect(screen.getByText(/currently active/i)).toBeInTheDocument();
  });

  it('shows owner-only trigger guidance for non-owners', async () => {
    server.use(
      http.get('*/api/projects/:projectId/discovery', () => {
        return HttpResponse.json({ jobs: [] });
      }),
    );

    render(<NodeDiscoveryPanel projectId={9} canTrigger={false} />);

    await waitFor(() => {
      expect(screen.getByText(/owner-only/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/cannot start new runs/i)).toBeInTheDocument();
  });

  it('submits exact password-mode trigger payload shape', async () => {
    let capturedBody: unknown = null;

    server.use(
      http.get('*/api/ssh-credentials', () => HttpResponse.json([])),
      http.get('*/api/projects/:projectId/discovery', () => HttpResponse.json({ jobs: [] })),
      http.post('*/api/projects/:projectId/discovery', async ({ params, request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({
          job: createDiscoveryJobResponse(Number(params.projectId), 'completed'),
        });
      }),
      http.get('*/api/projects/:projectId/discovery/:jobId', ({ params }) => {
        return HttpResponse.json(createDiscoveryJobResponse(Number(params.projectId), 'completed', { id: Number(params.jobId) }));
      }),
    );

    const user = userEvent.setup();
    render(<NodeDiscoveryPanel projectId={15} />);

    await user.type(screen.getByLabelText(/host ip addresses/i), '10.0.0.10,10.0.0.11');
    await user.type(screen.getByLabelText(/password/i), 'super-secret');
    await user.click(screen.getByRole('button', { name: /run discovery/i }));

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody).toEqual({
      nodes: [{ ip_address: '10.0.0.10' }, { ip_address: '10.0.0.11' }],
      ssh_username: 'root',
      ssh_port: 22,
      ssh_auth_type: 'password',
      ssh_password: 'super-secret',
    });
  });

  it('submits exact saved-credential trigger payload shape', async () => {
    let capturedBody: unknown = null;

    server.use(
      http.get('*/api/ssh-credentials', () =>
        HttpResponse.json([
          {
            id: 7,
            name: 'lab-ssh',
            host: '10.10.10.10',
            port: 22,
            username: 'ubuntu',
            auth_type: 'key',
          },
        ]),
      ),
      http.get('*/api/projects/:projectId/discovery', () => HttpResponse.json({ jobs: [] })),
      http.post('*/api/projects/:projectId/discovery', async ({ params, request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({
          job: createDiscoveryJobResponse(Number(params.projectId), 'completed', { ssh_credential_id: 7 }),
        });
      }),
      http.get('*/api/projects/:projectId/discovery/:jobId', ({ params }) => {
        return HttpResponse.json(
          createDiscoveryJobResponse(Number(params.projectId), 'completed', {
            id: Number(params.jobId),
            ssh_credential_id: 7,
          }),
        );
      }),
    );

    const user = userEvent.setup();
    render(<NodeDiscoveryPanel projectId={16} />);

    await user.type(screen.getByLabelText(/host ip addresses/i), '10.20.0.4');

    const [authMethodSelect] = screen.getAllByRole('combobox');
    await user.click(authMethodSelect);
    await user.click(screen.getByRole('option', { name: /saved ssh credential/i }));

    const selects = screen.getAllByRole('combobox');
    const credentialSelect = selects[selects.length - 1];
    expect(screen.getByRole('button', { name: /run discovery/i })).toBeDisabled();
    await user.click(credentialSelect);
    await user.click(screen.getByRole('option', { name: /lab-ssh/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /run discovery/i })).toBeEnabled();
    });

    await user.click(screen.getByRole('button', { name: /run discovery/i }));

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody).toEqual({
      nodes: [{ ip_address: '10.20.0.4' }],
      ssh_credential_id: 7,
    });
  });

  it('emits terminal completion feedback once for a completed trigger run', async () => {
    const successSpy = vi.spyOn(notify, 'success').mockImplementation(() => {});
    const infoSpy = vi.spyOn(notify, 'info').mockImplementation(() => {});

    server.use(
      http.get('*/api/ssh-credentials', () => HttpResponse.json([])),
      http.get('*/api/projects/:projectId/discovery', () => HttpResponse.json({ jobs: [] })),
      http.post('*/api/projects/:projectId/discovery', async ({ params }) => {
        return HttpResponse.json({
          job: createDiscoveryJobResponse(Number(params.projectId), 'completed'),
        });
      }),
      http.get('*/api/projects/:projectId/discovery/:jobId', ({ params }) => {
        return HttpResponse.json(createDiscoveryJobResponse(Number(params.projectId), 'completed', { id: Number(params.jobId) }));
      }),
    );

    const user = userEvent.setup();
    render(<NodeDiscoveryPanel projectId={17} />);

    await user.type(screen.getByLabelText(/host ip addresses/i), '10.0.0.44');
    await user.type(screen.getByLabelText(/password/i), 'secret');
    await user.click(screen.getByRole('button', { name: /run discovery/i }));

    await waitFor(() => {
      expect(successSpy).toHaveBeenCalledTimes(1);
    });
    expect(infoSpy).toHaveBeenCalledTimes(1);

    successSpy.mockRestore();
    infoSpy.mockRestore();
  });

});
