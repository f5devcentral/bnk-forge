/**
 * Tests for BenchmarkAgentsTab.
 *
 * Asserts:
 *   - Built-in agent (forge-local / builtin:true) is badged "Built-in"
 *   - Built-in agent appears first in the list
 *   - curl guide section is collapsed by default (Advanced accordion)
 *   - curl guide content is accessible after expanding the accordion
 *   - External agents (no builtin tag) render without the Built-in badge
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { BenchmarkAgentsTab } from '@/pages/BenchmarkAgentsTab';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

// ──────────────────────────────────────────────────────────────────────────────
// Fixtures
// ──────────────────────────────────────────────────────────────────────────────

const builtinAgent = {
  id: 1,
  name: 'forge-local',
  hostname: 'forge-agent',
  ip_address: '172.17.0.2',
  tags: { builtin: true },
  capabilities: { aiperf: '0.3.1' },
  status: 'connected',
  last_heartbeat: new Date().toISOString(),
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const externalAgent = {
  id: 2,
  name: 'loadgen-01',
  hostname: 'bench-host',
  ip_address: '10.0.0.1',
  tags: { role: 'load-gen' },
  capabilities: { aiperf: '0.3.0' },
  status: 'disconnected',
  last_heartbeat: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

function setupHandlers(agents: object[]) {
  server.use(
    http.get('*/api/benchmarks/agents', () => HttpResponse.json(agents)),
    http.get('*/api/benchmarks/agent-hosts', () => HttpResponse.json([])),
    http.delete('*/api/benchmarks/agents/:id', () => new HttpResponse(null, { status: 204 })),
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Built-in agent badge
// ──────────────────────────────────────────────────────────────────────────────

describe('BenchmarkAgentsTab — built-in agent', () => {
  it('badges the forge-local agent as "Built-in"', async () => {
    setupHandlers([builtinAgent]);
    render(<BenchmarkAgentsTab />);
    await waitFor(() => {
      expect(screen.getByText('forge-local')).toBeInTheDocument();
      // The badge reads "Built-in • control/demo only"
      expect(screen.getByText('Built-in • control/demo only')).toBeInTheDocument();
    });
  });

  it('does NOT badge an external agent as Built-in', async () => {
    setupHandlers([externalAgent]);
    render(<BenchmarkAgentsTab />);
    await waitFor(() => {
      expect(screen.getByText('loadgen-01')).toBeInTheDocument();
      expect(screen.queryByText('Built-in • control/demo only')).not.toBeInTheDocument();
    });
  });

  it('builtin agent appears first when mixed list', async () => {
    setupHandlers([externalAgent, builtinAgent]);
    render(<BenchmarkAgentsTab />);
    await waitFor(() => {
      const rows = screen.getAllByRole('row');
      // First data row (after header) should contain forge-local
      const firstDataRow = rows[1];
      expect(firstDataRow).toHaveTextContent('forge-local');
    });
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// Advanced accordion (curl guide)
// ──────────────────────────────────────────────────────────────────────────────

describe('BenchmarkAgentsTab — Advanced accordion', () => {
  it('curl guide accordion is collapsed by default', async () => {
    setupHandlers([builtinAgent]);
    render(<BenchmarkAgentsTab />);
    await waitFor(() => {
      // The accordion trigger should be present
      expect(screen.getByTestId('advanced-agent-accordion')).toBeInTheDocument();
    });
    // Content (Step 0 bearer token) should NOT be visible yet
    expect(screen.queryByText(/step 0/i)).not.toBeInTheDocument();
  });

  it('curl guide is visible after expanding the accordion', async () => {
    const user = userEvent.setup();
    setupHandlers([builtinAgent]);
    render(<BenchmarkAgentsTab />);

    await waitFor(() => {
      expect(screen.getByTestId('advanced-agent-accordion')).toBeInTheDocument();
    });

    await user.click(screen.getByTestId('advanced-agent-accordion'));

    await waitFor(() => {
      expect(screen.getByText(/step 0/i)).toBeInTheDocument();
      expect(screen.getByText(/step 1/i)).toBeInTheDocument();
    });
  });
});
