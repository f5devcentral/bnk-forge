/**
 * Tests for RecoveryPanel component
 *
 * Tests the two recovery workflows:
 *   1. CWC cert re-sync — status check, trigger resync, verify steps
 *   2. Platform restart — controller/FLO/TMM restart with checkboxes
 *
 * MSW handlers return realistic response shapes from the recovery backend.
 *
 * Backend: routes/k8s/recovery.py
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { RecoveryPanel } from '../RecoveryPanel';
import type {
  RecoveryStatusResponse,
  CWCCertResyncResponse,
  PlatformRestartResponse,
  PlatformRestartRequest,
} from '@/types';

// ---------------------------------------------------------------------------
// Fixtures — realistic API responses
// ---------------------------------------------------------------------------

const mockStatusHealthy: RecoveryStatusResponse = {
  cwc_cert_stale: false,
  vlans_failed: false,
  cwc_cert_detail: 'cwc-license-certs matches cert-manager — OK',
  vlans_detail: 'All 2 VLANs programmed — OK',
  platform_healthy: true,
};

const mockStatusIssues: RecoveryStatusResponse = {
  cwc_cert_stale: true,
  vlans_failed: true,
  cwc_cert_detail: 'cwc-license-certs has stale certs (doesn\'t match cert-manager)',
  vlans_detail: 'sf-external: Failure in sending CR config to grpc endpoints',
  platform_healthy: false,
};

const mockResyncSuccess: CWCCertResyncResponse = {
  success: true,
  message: 'CWC certs re-synced from cert-manager.',
  steps: [
    { step: 'read_cert_manager_cert', status: 'ok' },
    { step: 'update_cwc_license_certs', status: 'ok' },
    { step: 'restart_cwc_pod', status: 'ok', detail: 'Deleted pod: f5-spk-cwc-old' },
    { step: 'cleanup_agent_pods', status: 'ok' },
  ],
};

const mockPlatformRestartSuccess: PlatformRestartResponse = {
  success: true,
  message: 'Restarted: CNE Controller',
  restarted: [
    {
      component: 'CNE Controller',
      status: 'restarted',
      deleted_pods: ['f5-cne-controller-84867b7854-rfnvv'],
      message: 'Deleted 1 controller pod(s). VLANs and routes will re-sync within ~60s.',
    },
  ],
};

// ---------------------------------------------------------------------------
// Helper — set up MSW handlers
// ---------------------------------------------------------------------------

function setupHandlers(
  statusResponse: RecoveryStatusResponse = mockStatusHealthy,
) {
  server.use(
    http.get('*/api/k8s/clusters/1/recovery/status', () =>
      HttpResponse.json(statusResponse),
    ),
    http.post('*/api/k8s/clusters/1/recovery/cwc-certs', () =>
      HttpResponse.json(mockResyncSuccess),
    ),
    http.post('*/api/k8s/clusters/1/recovery/platform-restart', () =>
      HttpResponse.json(mockPlatformRestartSuccess),
    ),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('RecoveryPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Status Display', () => {
    it('shows healthy status when no issues detected', async () => {
      setupHandlers(mockStatusHealthy);
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/Platform Healthy/)).toBeInTheDocument();
      });
      expect(screen.getByText(/No recovery needed/)).toBeInTheDocument();
    });

    it('shows issues when problems detected', async () => {
      setupHandlers(mockStatusIssues);
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/Recovery Actions Available/)).toBeInTheDocument();
      });
      expect(screen.getByText(/CWC certs are stale/)).toBeInTheDocument();
      expect(screen.getByText(/VLANs have programming failures/)).toBeInTheDocument();
    });

    it('shows loading state initially', () => {
      setupHandlers();
      render(<RecoveryPanel clusterId={1} />);
      expect(screen.getByText(/Checking recovery status/)).toBeInTheDocument();
    });
  });

  describe('CWC Cert Recovery', () => {
    it('renders the CWC cert recovery card', async () => {
      setupHandlers();
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('CWC Certificate Recovery')).toBeInTheDocument();
      });
      expect(screen.getByText(/Re-sync CWC Certs/)).toBeInTheDocument();
    });

    it('shows stale badge when certs are stale', async () => {
      setupHandlers(mockStatusIssues);
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Stale')).toBeInTheDocument();
      });
    });

    it('triggers re-sync and shows step results', async () => {
      setupHandlers(mockStatusIssues);
      const user = userEvent.setup();
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/Re-sync CWC Certs/)).toBeInTheDocument();
      });

      await user.click(screen.getByText(/Re-sync CWC Certs/));

      await waitFor(() => {
        // Steps should appear
        expect(screen.getByText(/read cert manager cert/)).toBeInTheDocument();
        expect(screen.getByText(/update cwc license certs/)).toBeInTheDocument();
        expect(screen.getByText(/restart cwc pod/)).toBeInTheDocument();
        expect(screen.getByText(/cleanup agent pods/)).toBeInTheDocument();
      });
    });
  });

  describe('Platform Recovery', () => {
    it('renders the platform recovery card', async () => {
      setupHandlers();
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Platform Recovery')).toBeInTheDocument();
      });
      expect(screen.getByText(/Restart Selected Components/)).toBeInTheDocument();
    });

    it('has CNE Controller checked by default', async () => {
      setupHandlers();
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('CNE Controller')).toBeInTheDocument();
      });

      const checkboxes = screen.getAllByRole('checkbox');
      // The controller checkbox should be checked
      expect(checkboxes[0]).toBeChecked();
    });

    it('triggers platform restart and shows results', async () => {
      let capturedBody: PlatformRestartRequest | null = null;
      server.use(
        http.get('*/api/k8s/clusters/1/recovery/status', () =>
          HttpResponse.json(mockStatusIssues),
        ),
        http.post('*/api/k8s/clusters/1/recovery/platform-restart', async ({ request }) => {
          capturedBody = (await request.json()) as PlatformRestartRequest;
          return HttpResponse.json(mockPlatformRestartSuccess);
        }),
      );

      const user = userEvent.setup();
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/Restart Selected Components/)).toBeInTheDocument();
      });

      await user.click(screen.getByText(/Restart Selected Components/));

      await waitFor(() => {
        // The result section should show the deleted pod name
        expect(screen.getByText(/f5-cne-controller-84867b7854-rfnvv/)).toBeInTheDocument();
      });

      // Verify payload shape matches backend schema
      expect(capturedBody).toMatchObject({
        restart_controller: true,
        restart_flo: false,
        restart_tmm: false,
      });
    });

    it('shows TMM warning in advanced options', async () => {
      setupHandlers();
      const user = userEvent.setup();
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/Show advanced options/)).toBeInTheDocument();
      });

      await user.click(screen.getByText(/Show advanced options/));

      expect(screen.getByText('TMM (Data Plane)')).toBeInTheDocument();
      expect(screen.getByText('Traffic Impact')).toBeInTheDocument();
      expect(screen.getByText('FLO Lifecycle Operator')).toBeInTheDocument();
    });

    it('disables restart button when no components selected', async () => {
      setupHandlers();
      const user = userEvent.setup();
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('CNE Controller')).toBeInTheDocument();
      });

      // Uncheck the default controller checkbox
      const checkboxes = screen.getAllByRole('checkbox');
      await user.click(checkboxes[0]); // uncheck controller

      const button = screen.getByText(/Restart Selected Components/);
      expect(button.closest('button')).toBeDisabled();
    });
  });

  describe('Info Section', () => {
    it('renders the help text', async () => {
      setupHandlers();
      render(<RecoveryPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/When do you need recovery/)).toBeInTheDocument();
      });
      expect(screen.getByText(/safe, idempotent/)).toBeInTheDocument();
    });
  });
});
