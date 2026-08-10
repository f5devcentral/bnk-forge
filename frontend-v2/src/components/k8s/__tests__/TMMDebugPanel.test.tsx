/**
 * Tests for TMMDebugPanel component
 *
 * Tests the multi-stage flow:
 *   1. Loading — TMM pod discovery
 *   2. No TMM pods — empty state
 *   3. TMM pods found — pod selector, category/command selectors
 *   4. No debug sidecar — warning state
 *   5. Command execution — tmctl with parsed table, bdt_cli raw output
 *
 * The panel uses a compact action bar: a Category dropdown selects the command
 * family (tmctl/bdt_cli/configview/netkvest/raw); a Command dropdown picks the
 * specific command for tmctl/bdt_cli. Output renders directly below the bar.
 *
 * MSW handlers return realistic response shapes from the TMM debug backend.
 *
 * Backend: routes/k8s/tmm_debug.py, services/tmm_debug_service.py
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { TMMDebugPanel } from '../TMMDebugPanel';

// Radix Select relies on pointer-capture APIs that jsdom doesn't implement.
if (!HTMLElement.prototype.hasPointerCapture) {
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.releasePointerCapture = () => {};
}

/** Open the Category dropdown (first combobox) and pick an option by label. */
async function selectCategory(
  user: ReturnType<typeof userEvent.setup>,
  label: RegExp,
) {
  await user.click(screen.getAllByRole('combobox')[0]);
  await user.click(await screen.findByRole('option', { name: label }));
}

// ---------------------------------------------------------------------------
// Fixtures — realistic API responses
// ---------------------------------------------------------------------------

const mockPodsWithDebug = {
  pods: [
    {
      name: 'f5-tmm-znvz2',
      namespace: 'f5-bnk',
      has_debug: true,
      containers: ['tmm', 'debug'],
      phase: 'Running',
    },
    {
      name: 'f5-tmm-abc12',
      namespace: 'f5-bnk',
      has_debug: true,
      containers: ['tmm', 'debug'],
      phase: 'Running',
    },
  ],
  count: 2,
  debug_available: 2,
};

const mockPodsNoDebug = {
  pods: [
    {
      name: 'f5-tmm-old',
      namespace: 'f5-bnk',
      has_debug: false,
      containers: ['tmm'],
      phase: 'Running',
    },
  ],
  count: 1,
  debug_available: 0,
};

const mockPodsEmpty = {
  pods: [],
  count: 0,
  debug_available: 0,
};

const mockTmctlResponse = {
  columns: ['name', 'clientside.bytes_in', 'clientside.bytes_out'],
  rows: [
    ['/Common/vs1', '12345', '67890'],
    ['/Common/vs2', '99999', '11111'],
  ],
  raw: 'name  clientside.bytes_in  clientside.bytes_out\n/Common/vs1  12345  67890\n/Common/vs2  99999  11111',
  stderr: '',
  exit_code: 0,
  duration_ms: 85,
  command: 'tmctl -d blade virtual_server_stat -s name,clientside.bytes_in,clientside.bytes_out -w 200',
};

const mockExecResponse = {
  stdout: 'ARP entry 10.1.1.1 at 00:11:22:33:44:55\n',
  stderr: '',
  exit_code: 0,
  duration_ms: 30,
  command: 'bdt_cli -u -s tmm0:8850 arp',
};

// ---------------------------------------------------------------------------
// Helper: set up MSW handlers
// ---------------------------------------------------------------------------

function setupHandlers(opts: {
  pods?: typeof mockPodsWithDebug;
  tmctlResponse?: typeof mockTmctlResponse;
  execResponse?: typeof mockExecResponse;
}) {
  const { pods = mockPodsWithDebug, tmctlResponse = mockTmctlResponse, execResponse = mockExecResponse } = opts;

  server.use(
    http.get('*/api/k8s/clusters/1/tmm-debug/pods', () => {
      return HttpResponse.json(pods);
    }),
    http.post('*/api/k8s/clusters/1/tmm-debug/tmctl', () => {
      return HttpResponse.json(tmctlResponse);
    }),
    http.post('*/api/k8s/clusters/1/tmm-debug/bdt', () => {
      return HttpResponse.json(execResponse);
    }),
    http.post('*/api/k8s/clusters/1/tmm-debug/exec', () => {
      return HttpResponse.json(execResponse);
    }),
    http.post('*/api/k8s/clusters/1/tmm-debug/configview/uuids', () => {
      return HttpResponse.json({
        uuids: ['default-net-external-vlan', 'default-net-internal-vlan'],
        raw: 'default-net-external-vlan\ndefault-net-internal-vlan\n',
        stderr: '',
        exit_code: 0,
        duration_ms: 40,
        command: 'configview list',
      });
    }),
    http.post('*/api/k8s/clusters/1/tmm-debug/configview', () => {
      return HttpResponse.json({
        stdout: 'request:[declTmm.vlan]:{name:"external"}',
        stderr: '',
        exit_code: 0,
        duration_ms: 25,
        command: 'configview uuid default-net-external-vlan',
      });
    }),
  );
}

// ============================================================================
// Tests
// ============================================================================

describe('TMMDebugPanel', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  // afterEach is handled by the test-utils cleanup

  describe('empty state', () => {
    it('shows empty state when no TMM pods found', async () => {
      setupHandlers({ pods: mockPodsEmpty });

      render(<TMMDebugPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('No TMM Pods Found')).toBeInTheDocument();
      });

      expect(
        screen.getByText(/No f5-tmm pods were found in this cluster/)
      ).toBeInTheDocument();
    });
  });

  describe('with TMM pods', () => {
    it('shows pod selector with auto-selected first debug pod', async () => {
      setupHandlers({});

      render(<TMMDebugPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('f5-tmm-znvz2')).toBeInTheDocument();
      });

      // Should show debug sidecar available badge
      expect(screen.getByText('Debug sidecar available')).toBeInTheDocument();
    });

    it('shows category and command selectors with tmctl defaults', async () => {
      setupHandlers({});

      render(<TMMDebugPanel clusterId={1} />);

      // Defaults to the tmctl category with the first command pre-selected;
      // both selected labels render inside their dropdown triggers.
      await waitFor(() => {
        expect(screen.getByText('Traffic Statistics (tmctl)')).toBeInTheDocument();
      });
      expect(screen.getByText('Virtual Server Stats')).toBeInTheDocument();

      // Description of the selected command is shown under the bar.
      expect(screen.getByText('Traffic bytes in/out for virtual servers')).toBeInTheDocument();

      // Two dropdowns (category + command) and a Run button.
      expect(screen.getAllByRole('combobox')).toHaveLength(2);
      expect(screen.getByRole('button', { name: /^Run$/ })).toBeInTheDocument();
    });

    it('lists tmctl commands in the command dropdown', async () => {
      setupHandlers({});
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<TMMDebugPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Traffic Statistics (tmctl)')).toBeInTheDocument();
      });

      // Open the command dropdown (second combobox).
      await user.click(screen.getAllByRole('combobox')[1]);

      // A representative spread of tmctl commands are options.
      expect(await screen.findByRole('option', { name: /Pool Member Stats/ })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: /TMM Stats/ })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: /DOCA Flow Entries/ })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: /DNS Cache/ })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: /DoS Stats/ })).toBeInTheDocument();
    });

    it('executes tmctl command and shows table output', async () => {
      setupHandlers({});
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<TMMDebugPanel clusterId={1} />);

      // Wait for pods to load — Virtual Server Stats is the default command
      await waitFor(() => {
        expect(screen.getByText('Virtual Server Stats')).toBeInTheDocument();
      });

      // Run the pre-selected tmctl command
      await user.click(screen.getByRole('button', { name: /^Run$/ }));

      // Wait for output
      await waitFor(() => {
        expect(screen.getByText('Output')).toBeInTheDocument();
      });

      // Table should show column headers
      expect(screen.getByText('name')).toBeInTheDocument();
      expect(screen.getByText('clientside.bytes_in')).toBeInTheDocument();
      expect(screen.getByText('clientside.bytes_out')).toBeInTheDocument();

      // Table should show data rows
      expect(screen.getByText('/Common/vs1')).toBeInTheDocument();
      expect(screen.getByText('12345')).toBeInTheDocument();
      expect(screen.getByText('67890')).toBeInTheDocument();

      // Row count
      expect(screen.getByText('2 rows')).toBeInTheDocument();
    });

    it('executes bdt_cli command and shows raw output', async () => {
      setupHandlers({});
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<TMMDebugPanel clusterId={1} />);

      // Wait for pods to load
      await waitFor(() => {
        expect(screen.getByText('Traffic Statistics (tmctl)')).toBeInTheDocument();
      });

      // Switch to the bdt_cli category — ARP Table becomes the default command
      await selectCategory(user, /Networking \(bdt_cli\)/);
      await waitFor(() => {
        expect(screen.getByText('ARP Table')).toBeInTheDocument();
      });

      // Run the pre-selected bdt_cli command
      await user.click(screen.getByRole('button', { name: /^Run$/ }));

      // Wait for output
      await waitFor(() => {
        expect(screen.getByText(/ARP entry 10.1.1.1/)).toBeInTheDocument();
      });

      // Should show command in the output area
      expect(screen.getByText(/bdt_cli -u -s tmm0:8850 arp/)).toBeInTheDocument();
    });

    it('lists configview UUIDs and hides the picker when switching category', async () => {
      setupHandlers({});
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<TMMDebugPanel clusterId={1} />);

      // Wait for pods to load
      await waitFor(() => {
        expect(screen.getByText('Traffic Statistics (tmctl)')).toBeInTheDocument();
      });

      // Switch to configview and list UUIDs
      await selectCategory(user, /Configuration \(configview\)/);
      await user.click(await screen.findByRole('button', { name: /List UUIDs/ }));

      // UUID picker appears with the returned UUIDs
      await waitFor(() => {
        expect(screen.getByText('Select Configuration UUID')).toBeInTheDocument();
      });
      expect(screen.getByText('default-net-external-vlan')).toBeInTheDocument();

      // Switching back to tmctl hides the picker
      await selectCategory(user, /Traffic Statistics \(tmctl\)/);
      expect(screen.queryByText('Select Configuration UUID')).not.toBeInTheDocument();
    });

    it('runs netkvest connectivity check', async () => {
      setupHandlers({});
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<TMMDebugPanel clusterId={1} />);

      // Wait for pods to load, then switch to the netkvest category
      await waitFor(() => {
        expect(screen.getByText('Traffic Statistics (tmctl)')).toBeInTheDocument();
      });
      await selectCategory(user, /Connectivity \(netkvest\)/);
      await waitFor(() => {
        expect(screen.getByText('Run Connectivity Check')).toBeInTheDocument();
      });

      // Fill in SNAT pool and destination
      const snatInput = screen.getByPlaceholderText(/egress-snatpool/);
      const destInput = screen.getByPlaceholderText(/22\.22\.22\.100/);

      await user.type(snatInput, 'my-snatpool');
      await user.type(destInput, '10.1.1.100');

      // Click Run Connectivity Check
      await user.click(screen.getByText('Run Connectivity Check'));

      // Wait for output
      await waitFor(() => {
        expect(screen.getByText('Output')).toBeInTheDocument();
      });
    });

    it('executes raw command from input', async () => {
      setupHandlers({});
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<TMMDebugPanel clusterId={1} />);

      // Wait for pods to load, then switch to the raw-command category
      await waitFor(() => {
        expect(screen.getByText('Traffic Statistics (tmctl)')).toBeInTheDocument();
      });
      await selectCategory(user, /Advanced — Raw Command/);

      // The raw command input appears once the category is selected
      const input = await screen.findByPlaceholderText(/Enter any debug sidecar command/);
      await user.type(input, 'tmctl -d blade tmm_stat');

      // Click Run button
      await user.click(screen.getByRole('button', { name: /^Run$/ }));

      // Wait for output
      await waitFor(() => {
        expect(screen.getByText('Output')).toBeInTheDocument();
      });
    });
  });

  describe('no debug sidecar', () => {
    it('shows warning when TMM pod lacks debug container', async () => {
      setupHandlers({ pods: mockPodsNoDebug });

      render(<TMMDebugPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Debug Sidecar Not Available')).toBeInTheDocument();
      });

      expect(
        screen.getByText(/does not have a debug container/)
      ).toBeInTheDocument();
    });

    it('does not show the action bar when debug sidecar is missing', async () => {
      setupHandlers({ pods: mockPodsNoDebug });

      render(<TMMDebugPanel clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Debug Sidecar Not Available')).toBeInTheDocument();
      });

      // The category/command action bar should NOT be rendered
      expect(screen.queryByText('Traffic Statistics (tmctl)')).not.toBeInTheDocument();
      expect(screen.queryByText('Virtual Server Stats')).not.toBeInTheDocument();
      expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    });
  });
});
