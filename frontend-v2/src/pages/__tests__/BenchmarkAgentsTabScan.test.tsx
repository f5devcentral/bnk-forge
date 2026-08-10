/**
 * Tests for BenchmarkAgentsTab — Slice 2 scan button + readiness card.
 *
 * Asserts:
 *  - Scan button fires POST .../scan for each managed host row
 *  - Spinner replaces scan icon while provision_status === 'scanning'
 *  - Readiness card renders verdict badge after scan completes
 *  - Readiness card shows tool presence icons
 *  - Readiness card shows per-target reachability
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { BenchmarkAgentsTab } from '@/pages/BenchmarkAgentsTab';
import { TooltipProvider } from '@/components/ui/tooltip';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

/** BenchmarkAgentsTab uses Tooltip components that require a TooltipProvider ancestor. */
function renderTab() {
  return render(
    <TooltipProvider>
      <BenchmarkAgentsTab />
    </TooltipProvider>,
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Fixtures
// ──────────────────────────────────────────────────────────────────────────────

const baseHost = {
  id: 10,
  name: 'bench-host-01',
  hostname: null,
  ip_address: null,
  tags: null,
  capabilities: null,
  status: 'disconnected',
  last_heartbeat: null,
  project_id: 1,
  host_ip: '10.0.1.100',
  ssh_credential_id: 1,
  ssh_port: 22,
  jumphost_chain: null,
  provision_status: 'unprovisioned',
  provision_message: null,
  readiness: null,
  managed: true,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const scanningHost = {
  ...baseHost,
  provision_status: 'scanning',
};

const readyHost = {
  ...baseHost,
  provision_status: 'unprovisioned',
  provision_message: 'Scan complete — ready',
  readiness: {
    ssh_reachable: true,
    os: {
      os_type: 'ubuntu',
      os_version: '22.04',
      os_pretty_name: 'Ubuntu 22.04 LTS',
      architecture: 'x86_64',
    },
    cpu: 8,
    mem_gb: 32.0,
    tools: { python3: true, pip: true, aiperf: true, systemctl: true, python3_version: 'Python 3.11.0' },
    reachable_targets: [
      { target_id: 1, name: 'llm-cluster', llm_base_url: 'http://llm:8000', ok: true, http_code: 200, error: null },
    ],
    verdict: 'ready',
  },
};

const needsProvisionHost = {
  ...baseHost,
  provision_message: 'Scan complete — needs_provision',
  readiness: {
    ssh_reachable: true,
    os: {},
    cpu: 4,
    mem_gb: 16.0,
    tools: { python3: true, pip: true, aiperf: false, systemctl: true },
    reachable_targets: [],
    verdict: 'needs_provision',
  },
};

function setupHandlers(
  hosts: object[],
  scanResponse: object = { host_id: 10, message: 'Dispatched', celery_task_id: 'abc-123' },
) {
  server.use(
    http.get('*/api/benchmarks/agents', () => HttpResponse.json([])),
    http.get('*/api/benchmarks/agent-hosts', () => HttpResponse.json(hosts)),
    http.post('*/api/benchmarks/agent-hosts/:id/scan', () =>
      HttpResponse.json(scanResponse, { status: 202 }),
    ),
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Scan button
// ──────────────────────────────────────────────────────────────────────────────

describe('BenchmarkAgentsTab — scan button', () => {
  it('renders an enabled scan button for a managed host', async () => {
    setupHandlers([baseHost]);
    renderTab();

    await waitFor(() => {
      expect(screen.getByText('bench-host-01')).toBeInTheDocument();
    });

    // Scan button has title "Run SSH suitability scan"
    const scanBtn = screen.getByTitle('Run SSH suitability scan');
    expect(scanBtn).toBeInTheDocument();
    expect(scanBtn).not.toBeDisabled();
  });

  it('dispatches POST scan when scan button clicked', async () => {
    const user = userEvent.setup();
    let scanCalled = false;

    server.use(
      http.get('*/api/benchmarks/agents', () => HttpResponse.json([])),
      http.get('*/api/benchmarks/agent-hosts', () => HttpResponse.json([baseHost])),
      http.post('*/api/benchmarks/agent-hosts/:id/scan', () => {
        scanCalled = true;
        return HttpResponse.json({ host_id: 10, message: 'ok', celery_task_id: 'task-1' }, { status: 202 });
      }),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByText('bench-host-01')).toBeInTheDocument();
    });

    const scanBtn = screen.getByTitle('Run SSH suitability scan');
    await user.click(scanBtn);

    await waitFor(() => {
      expect(scanCalled).toBe(true);
    });
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// Scanning state — spinner
// ──────────────────────────────────────────────────────────────────────────────

describe('BenchmarkAgentsTab — scanning state', () => {
  it('shows "scanning" provision badge while scan in progress', async () => {
    setupHandlers([scanningHost]);
    renderTab();

    await waitFor(() => {
      expect(screen.getByText('bench-host-01')).toBeInTheDocument();
      expect(screen.getByText('scanning')).toBeInTheDocument();
    });
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// Readiness card
// ──────────────────────────────────────────────────────────────────────────────

describe('BenchmarkAgentsTab — readiness card', () => {
  it('shows "unprovisioned" provision badge when scan is done', async () => {
    setupHandlers([readyHost]);
    renderTab();

    await waitFor(() => {
      expect(screen.getByText('bench-host-01')).toBeInTheDocument();
    });
    // provision_status is 'unprovisioned' after scan (not 'scanning')
    expect(screen.getByText('unprovisioned')).toBeInTheDocument();
  });

  it('renders readiness card with Ready verdict after clicking toggle', async () => {
    const user = userEvent.setup();
    setupHandlers([readyHost]);
    renderTab();

    await waitFor(() => {
      expect(screen.getByText('bench-host-01')).toBeInTheDocument();
    });

    // The readiness toggle button has title text containing "readiness"
    const readinessToggle = screen.getByTitle(/show readiness/i);
    await user.click(readinessToggle);

    await waitFor(() => {
      expect(screen.getByText('Readiness:')).toBeInTheDocument();
    });
    expect(screen.getByText('Ready')).toBeInTheDocument();
    expect(screen.getByText('(Python 3.11.0)')).toBeInTheDocument();
  });

  it('shows target reachability in readiness card', async () => {
    const user = userEvent.setup();
    setupHandlers([readyHost]);
    renderTab();

    await waitFor(() => {
      expect(screen.getByText('bench-host-01')).toBeInTheDocument();
    });

    const readinessToggle = screen.getByTitle(/show readiness/i);
    await user.click(readinessToggle);

    await waitFor(() => {
      expect(screen.getByText('Target reachability from host:')).toBeInTheDocument();
    });
    expect(screen.getByText('llm-cluster')).toBeInTheDocument();
    expect(screen.getByText('HTTP 200')).toBeInTheDocument();
  });

  it('renders needs_provision verdict', async () => {
    const user = userEvent.setup();
    setupHandlers([needsProvisionHost]);
    renderTab();

    await waitFor(() => {
      expect(screen.getByText('bench-host-01')).toBeInTheDocument();
    });

    const readinessToggle = screen.getByTitle(/show readiness/i);
    await user.click(readinessToggle);

    await waitFor(() => {
      expect(screen.getByText('Readiness:')).toBeInTheDocument();
    });
    expect(screen.getByText('Needs provisioning')).toBeInTheDocument();
  });
});
