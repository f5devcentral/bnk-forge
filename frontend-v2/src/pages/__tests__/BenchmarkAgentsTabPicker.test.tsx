/**
 * Slice 5: BenchmarkAgentsTab — host/jumphost picker tests.
 *
 * Tests what can reliably be exercised within jsdom + Radix constraints.
 * The Radix Select component requires pointer events that jsdom doesn't support
 * (hasPointerCapture), so tests that need to click a Select item use fireEvent
 * directly or test the hook/API layer only.
 *
 * Asserts:
 *   - "Register remote host" button opens dialog
 *   - Dialog renders name field + project selector
 *   - useAgentHostCandidates hook fetches from the correct endpoint
 *   - import-aws-jumphost API method sends correct payload
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { BenchmarkAgentsTab } from '@/pages/BenchmarkAgentsTab';
import { useAgentHostCandidates } from '@/hooks/useBenchmarks';
import { benchmarksApi } from '@/lib/api/benchmarks';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

// ──────────────────────────────────────────────────────────────────────────────
// Fixtures (real backend response shapes)
// ──────────────────────────────────────────────────────────────────────────────

const PROJECT_ID = 42;

const bmCandidate = {
  label: 'bm-host-01 (10.1.2.3)',
  host_ip: '10.1.2.3',
  ssh_credential_id: 5,
  ssh_port: 22,
  jumphost_chain: null,
  source: 'bare_metal',
  source_ref: '17',
  last_test_status: null,
  needs_credential_import: false,
  infra_key_path: null,
  module_id: null,
};

const awsCandidate = {
  label: 'AWS jumphost — infra-module (54.1.2.3)',
  host_ip: '54.1.2.3',
  ssh_credential_id: null,
  ssh_port: 22,
  jumphost_chain: null,
  source: 'aws_jumphost',
  source_ref: '8',
  last_test_status: null,
  needs_credential_import: true,
  infra_key_path: '/app/state/42/8/infrastructure/infrastructure-access.pem',
  module_id: 8,
};

const candidatesResponse = {
  project_id: PROJECT_ID,
  candidates: [awsCandidate, bmCandidate],
};

function setupBaseHandlers() {
  server.use(
    http.get('*/api/benchmarks/agents', () => HttpResponse.json([])),
    http.get('*/api/benchmarks/agent-hosts', () => HttpResponse.json([])),
    http.get('*/api/ssh-credentials', () => HttpResponse.json([])),
    http.get('*/api/projects', () =>
      HttpResponse.json({ projects: [{ id: PROJECT_ID, name: 'aws-prod' }], total: 1 }),
    ),
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Component-level tests (dialog open/close)
// ──────────────────────────────────────────────────────────────────────────────

describe('BenchmarkAgentsTab — register dialog', () => {
  it('opens the register dialog when the button is clicked', async () => {
    const user = userEvent.setup();
    setupBaseHandlers();
    server.use(
      http.get('*/api/benchmarks/agent-host-candidates', () => HttpResponse.json(candidatesResponse)),
    );

    render(<BenchmarkAgentsTab />);

    await waitFor(() => {
      expect(screen.getByText('Register remote host')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Register remote host'));

    await waitFor(() => {
      expect(screen.getByText('Register remote agent host')).toBeInTheDocument();
      expect(screen.getByLabelText('Name')).toBeInTheDocument();
    });
  });

  it('dialog has project selector and name field', async () => {
    const user = userEvent.setup();
    setupBaseHandlers();
    server.use(
      http.get('*/api/benchmarks/agent-host-candidates', () => HttpResponse.json(candidatesResponse)),
    );

    render(<BenchmarkAgentsTab />);
    await user.click(await screen.findByText('Register remote host'));

    await waitFor(() => {
      expect(screen.getByLabelText('Name')).toBeInTheDocument();
      // Project Select trigger should be present
      expect(screen.getByText('Select a project…')).toBeInTheDocument();
    });
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// Hook-level tests (useAgentHostCandidates)
// ──────────────────────────────────────────────────────────────────────────────

describe('useAgentHostCandidates', () => {
  function createWrapper() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    return ({ children }: { children: React.ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);
  }

  it('fetches candidates from the correct endpoint', async () => {
    let capturedUrl = '';
    server.use(
      http.get('*/api/benchmarks/agent-host-candidates', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json(candidatesResponse);
      }),
    );

    const { result } = renderHook(() => useAgentHostCandidates(PROJECT_ID), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(capturedUrl).toContain(`project_id=${PROJECT_ID}`);
    expect(result.current.data?.candidates).toHaveLength(2);
  });

  it('returns candidates grouped correctly by source', async () => {
    server.use(
      http.get('*/api/benchmarks/agent-host-candidates', () => HttpResponse.json(candidatesResponse)),
    );

    const { result } = renderHook(() => useAgentHostCandidates(PROJECT_ID), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const candidates = result.current.data!.candidates;
    const sources = candidates.map(c => c.source);
    expect(sources).toContain('aws_jumphost');
    expect(sources).toContain('bare_metal');

    const aws = candidates.find(c => c.source === 'aws_jumphost');
    expect(aws?.host_ip).toBe('54.1.2.3');
    expect(aws?.needs_credential_import).toBe(true);
    expect(aws?.ssh_credential_id).toBeNull();

    const bm = candidates.find(c => c.source === 'bare_metal');
    expect(bm?.host_ip).toBe('10.1.2.3');
    expect(bm?.ssh_credential_id).toBe(5);
    expect(bm?.needs_credential_import).toBe(false);
  });

  it('is disabled when no project_id', async () => {
    let fetchCalled = false;
    server.use(
      http.get('*/api/benchmarks/agent-host-candidates', () => {
        fetchCalled = true;
        return HttpResponse.json(candidatesResponse);
      }),
    );

    const { result } = renderHook(() => useAgentHostCandidates(undefined), {
      wrapper: createWrapper(),
    });

    // Give it a tick to confirm no fetch
    await new Promise(r => setTimeout(r, 50));
    expect(fetchCalled).toBe(false);
    expect(result.current.data).toBeUndefined();
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// API-layer tests (benchmarksApi)
// ──────────────────────────────────────────────────────────────────────────────

describe('benchmarksApi — agent-host-candidates', () => {
  it('listAgentHostCandidates sends correct query param', async () => {
    let capturedUrl = '';
    server.use(
      http.get('*/api/benchmarks/agent-host-candidates', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json(candidatesResponse);
      }),
    );

    const result = await benchmarksApi.listAgentHostCandidates(PROJECT_ID);
    expect(capturedUrl).toContain(`project_id=${PROJECT_ID}`);
    expect(result.project_id).toBe(PROJECT_ID);
    expect(result.candidates).toHaveLength(2);
  });

  it('importAwsJumphost sends correct payload', async () => {
    let body: unknown;
    server.use(
      http.post('*/api/benchmarks/agent-host-candidates/import-aws-jumphost', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ssh_credential_id: 99 }, { status: 201 });
      }),
    );

    const result = await benchmarksApi.importAwsJumphost({ project_id: PROJECT_ID, module_id: 8 });
    expect(body).toEqual({ project_id: PROJECT_ID, module_id: 8 });
    expect(result.ssh_credential_id).toBe(99);
  });
});
