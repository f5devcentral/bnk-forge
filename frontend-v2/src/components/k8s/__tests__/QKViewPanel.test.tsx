/**
 * Tests for QKViewPanel component
 *
 * Tests the multi-stage flow:
 *   1. Loading — CWC availability check
 *   2. CWC Not Available — warning state
 *   3. Setup Required — cert-manager setup flow
 *   4. Ready — QKView list, create form, empty state
 *
 * MSW handlers return realistic response shapes from the QKView backend.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { QKViewPanel } from '../QKViewPanel';

// ---------------------------------------------------------------------------
// Fixtures — realistic API responses
// ---------------------------------------------------------------------------

const mockCheckAvailable = { available: true, message: 'CWC is available' };
const mockCheckUnavailable = { available: false, message: 'CWC pod not found in f5-utils namespace' };

const mockSetupComplete = {
  setup_complete: true,
  cert_manager_available: true,
  server_cert_exists: true,
  client_cert_exists: true,
  message: 'Setup complete',
};

const mockSetupRequired = {
  setup_complete: false,
  cert_manager_available: true,
  server_cert_exists: false,
  client_cert_exists: false,
  message: 'Certificate setup required for CWC REST API access',
};

const mockSetupNoCertManager = {
  setup_complete: false,
  cert_manager_available: false,
  server_cert_exists: false,
  client_cert_exists: false,
  message: 'cert-manager not available',
};

const mockQKViewList = {
  qkviews: [
    {
      id: 'qkview-12345',
      filename: 'diag-2026-02-28',
      namespace: '',
      status: 'Completed',
    },
    {
      id: 'qkview-67890',
      filename: 'troubleshoot-vlan',
      namespace: 'f5-operator',
      status: 'In Progress',
    },
  ],
};

const mockQKViewListEmpty = { qkviews: [] };

// ---------------------------------------------------------------------------
// Helper: set up MSW handlers for a given state
// ---------------------------------------------------------------------------

function setupHandlers(opts: {
  available?: boolean;
  setupComplete?: boolean;
  certManagerAvailable?: boolean;
  qkviews?: typeof mockQKViewList;
}) {
  const {
    available = true,
    setupComplete = true,
    certManagerAvailable = true,
    qkviews = mockQKViewList,
  } = opts;

  server.use(
    http.get(/\/api\/qkview\/check/, () => {
      return HttpResponse.json(available ? mockCheckAvailable : mockCheckUnavailable);
    }),
    // Setup status moved to /api/licensing/{clusterId}/cwc-setup-status (commit 10378887)
    http.get(/\/api\/licensing\/\d+\/cwc-setup-status/, () => {
      if (!available) return HttpResponse.json(mockSetupRequired);
      if (setupComplete) return HttpResponse.json(mockSetupComplete);
      if (!certManagerAvailable) return HttpResponse.json(mockSetupNoCertManager);
      return HttpResponse.json(mockSetupRequired);
    }),
    http.get(/\/api\/qkview\/list/, () => {
      return HttpResponse.json(qkviews);
    }),
  );
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  // Default: CWC available, setup complete, has QKViews
  setupHandlers({});
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('QKViewPanel', () => {
  // ─── Loading State ──────────────────────────────────────────────────

  describe('loading state', () => {
    it('shows loading message while checking CWC availability', () => {
      server.use(
        http.get(/\/api\/qkview\/check/, async () => {
          await new Promise((r) => setTimeout(r, 10000));
          return HttpResponse.json(mockCheckAvailable);
        }),
      );

      render(<QKViewPanel clusterId={1} />);
      expect(screen.getByText('Checking CWC availability...')).toBeInTheDocument();
    });

    it('shows setup status loading when CWC is available', () => {
      server.use(
        http.get(/\/api\/qkview\/check/, () => {
          return HttpResponse.json(mockCheckAvailable);
        }),
        http.get(/\/api\/licensing\/\d+\/cwc-setup-status/, async () => {
          await new Promise((r) => setTimeout(r, 10000));
          return HttpResponse.json(mockSetupComplete);
        }),
      );

      render(<QKViewPanel clusterId={1} />);
      // Loading text appears for one of the checks
      expect(
        screen.getByText(/Checking CWC availability|Checking setup status/)
      ).toBeInTheDocument();
    });
  });

  // ─── CWC Not Available ──────────────────────────────────────────────

  describe('CWC not available', () => {
    it('shows "CWC Not Available" heading when CWC is unavailable', async () => {
      setupHandlers({ available: false });

      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/CWC Not Available/)).toBeInTheDocument();
      });
    });

    it('shows CWC error message from API', async () => {
      setupHandlers({ available: false });

      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/CWC pod not found/)).toBeInTheDocument();
      });
    });
  });

  // ─── Setup Required ────────────────────────────────────────────────

  describe('setup required', () => {
    it('shows "Certificate Setup Required" heading', async () => {
      setupHandlers({ setupComplete: false });

      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Certificate Setup Required')).toBeInTheDocument();
      });
    });

    it('shows setup confirmation checkbox', async () => {
      setupHandlers({ setupComplete: false });

      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/I understand the CWC pod will be restarted/)).toBeInTheDocument();
      });
    });

    it('shows "Run Setup" button (disabled until confirmed)', async () => {
      setupHandlers({ setupComplete: false });

      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        const btn = screen.getByRole('button', { name: /Run Setup/i });
        expect(btn).toBeInTheDocument();
        expect(btn).toBeDisabled();
      });
    });

    it('enables "Run Setup" after confirmation checkbox is checked', async () => {
      setupHandlers({ setupComplete: false });
      const user = userEvent.setup();

      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/I understand the CWC pod/)).toBeInTheDocument();
      });

      const checkbox = screen.getByRole('checkbox');
      await user.click(checkbox);

      const btn = screen.getByRole('button', { name: /Run Setup/i });
      expect(btn).not.toBeDisabled();
    });

    it('shows setup steps description', async () => {
      setupHandlers({ setupComplete: false });

      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('This setup will:')).toBeInTheDocument();
        expect(screen.getByText(/cwc-license-certs/)).toBeInTheDocument();
        expect(screen.getByText(/Restart the CWC pod/)).toBeInTheDocument();
      });
    });
  });

  // ─── No cert-manager ───────────────────────────────────────────────

  describe('cert-manager not available', () => {
    it('shows "cert-manager not available" error', async () => {
      setupHandlers({ setupComplete: false, certManagerAvailable: false });

      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        // Two elements match: the setup message and the inline error heading
        const matches = screen.getAllByText('cert-manager not available');
        expect(matches.length).toBeGreaterThanOrEqual(1);
      });
    });

    it('mentions bnk-ca-cluster-issuer requirement', async () => {
      setupHandlers({ setupComplete: false, certManagerAvailable: false });

      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/bnk-ca-cluster-issuer/)).toBeInTheDocument();
      });
    });
  });

  // ─── Ready State — QKView List ─────────────────────────────────────

  describe('ready state', () => {
    it('shows "QKView Diagnostics" header', async () => {
      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('QKView Diagnostics')).toBeInTheDocument();
      });
    });

    it('shows "New QKView" button', async () => {
      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('New QKView')).toBeInTheDocument();
      });
    });

    it('shows "Refresh" button', async () => {
      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Refresh')).toBeInTheDocument();
      });
    });

    it('links to F5 iHealth', async () => {
      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('F5 iHealth')).toBeInTheDocument();
      });
    });

    it('shows "QKView Collections" section title', async () => {
      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('QKView Collections')).toBeInTheDocument();
      });
    });
  });

  // ─── Empty QKView List ─────────────────────────────────────────────

  describe('empty QKView list', () => {
    it('shows empty state message when no QKViews exist', async () => {
      setupHandlers({ qkviews: mockQKViewListEmpty });

      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/No QKView collections yet/)).toBeInTheDocument();
      });
    });
  });

  // ─── Create Form ───────────────────────────────────────────────────

  describe('create form', () => {
    it('toggles create form when "New QKView" is clicked', async () => {
      const user = userEvent.setup();
      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('New QKView')).toBeInTheDocument();
      });

      await user.click(screen.getByText('New QKView'));

      await waitFor(() => {
        expect(screen.getByText('Create QKView Collection')).toBeInTheDocument();
      });
    });

    it('shows filename input in create form', async () => {
      const user = userEvent.setup();
      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('New QKView')).toBeInTheDocument();
      });

      await user.click(screen.getByText('New QKView'));

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/troubleshoot-vlan/)).toBeInTheDocument();
      });
    });

    it('shows namespace input in create form', async () => {
      const user = userEvent.setup();
      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('New QKView')).toBeInTheDocument();
      });

      await user.click(screen.getByText('New QKView'));

      await waitFor(() => {
        expect(screen.getByPlaceholderText('All namespaces')).toBeInTheDocument();
      });
    });

    it('shows "Collect QKView" submit button', async () => {
      const user = userEvent.setup();
      render(<QKViewPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('New QKView')).toBeInTheDocument();
      });

      await user.click(screen.getByText('New QKView'));

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Collect QKView/i })).toBeInTheDocument();
      });
    });
  });
});
