/**
 * Tests for ClusterStatusBadge — the unified cluster reachability indicator.
 *
 * Covers:
 *   - reads state from useTargetConnectivity (single source of truth)
 *   - cold start (no probe data) renders 'Checking'
 *   - reachable / unreachable from registry render correctly
 *   - SSH-tunnel-enabled clusters route to the SSH probe (Phase 2)
 *   - tooltip carries the suggested_action verbatim from the backend
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { ClusterStatusBadge } from '../ClusterStatusBadge';

vi.mock('@/hooks/useConnectivity', () => ({
  useTargetConnectivity: vi.fn(),
}));

import { useTargetConnectivity } from '@/hooks/useConnectivity';

const mockUseTargetConnectivity = vi.mocked(useTargetConnectivity);

function makeReturn(state?: {
  state: 'reachable' | 'unreachable' | 'unknown';
  error_context?: { target_name?: string; suggested_action?: string };
}) {
  return {
    state: state
      ? {
          target_type: 'cluster',
          target_id: 1,
          target_name: state.error_context?.target_name ?? null,
          state: state.state,
          latency_ms: null,
          error_category: null,
          error_context: state.error_context ?? {},
          checked_at: '2026-05-04T00:00:00Z',
        }
      : undefined,
    isReachable: state?.state === 'reachable',
    isUnreachable: state?.state === 'unreachable',
    isUnknown: !state || state?.state === 'unknown',
    retry: vi.fn(),
  };
}

function setRegistryState(state?: Parameters<typeof makeReturn>[0]) {
  mockUseTargetConnectivity.mockReturnValue(makeReturn(state));
}

describe('ClusterStatusBadge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders Checking state when no probe data has landed yet', () => {
    setRegistryState(undefined);
    render(<ClusterStatusBadge cluster={{ id: 1, name: 'edge-east' }} />);
    expect(screen.getByLabelText(/edge-east — Checking/)).toBeInTheDocument();
  });

  it('renders Reachable when registry says reachable', () => {
    setRegistryState({
      state: 'reachable',
      error_context: { target_name: 'edge-east' },
    });
    render(<ClusterStatusBadge cluster={{ id: 1, name: 'edge-east' }} />);
    expect(screen.getByLabelText(/edge-east — Reachable/)).toBeInTheDocument();
  });

  it('renders Offline AND surfaces the suggested_action verbatim', () => {
    setRegistryState({
      state: 'unreachable',
      error_context: {
        target_name: 'aws-syd',
        suggested_action: 'Check VPN connection — IP changed?',
      },
    });
    render(<ClusterStatusBadge cluster={{ id: 1, name: 'aws-syd' }} />);
    const el = screen.getByLabelText(/aws-syd — Offline/);
    expect(el).toBeInTheDocument();
    expect(el).toHaveAttribute(
      'title',
      expect.stringContaining('Check VPN connection — IP changed?'),
    );
  });

  it('renders the state text label when showStatusLabel is true', () => {
    setRegistryState({
      state: 'unreachable',
      error_context: { target_name: 'aws-syd', suggested_action: 'Check VPN.' },
    });
    render(
      <ClusterStatusBadge cluster={{ id: 1, name: 'aws-syd' }} showStatusLabel />,
    );
    expect(screen.getByText('Offline')).toBeInTheDocument();
  });

  it("queries 'ssh' probe by ssh_credential_id when ssh_tunnel_enabled", () => {
    // Phase-2 SSH probe lives in the same registry — the badge should ask
    // for ('ssh', cred_id) instead of ('cluster', id) for tunneled clusters.
    setRegistryState({
      state: 'reachable',
      error_context: { target_name: 'jumphost-1' },
    });
    render(
      <ClusterStatusBadge
        cluster={{
          id: 1,
          name: 'on-prem-1',
          ssh_tunnel_enabled: true,
          ssh_credential_id: 42,
        }}
      />,
    );
    expect(mockUseTargetConnectivity).toHaveBeenCalledWith('ssh', 42);
    const el = screen.getByLabelText(/on-prem-1 — Reachable/);
    expect(el).toHaveAttribute('title', expect.stringContaining('SSH tunnel'));
  });

  it("queries 'cluster' probe by cluster id when not tunneled", () => {
    setRegistryState({ state: 'reachable' });
    render(<ClusterStatusBadge cluster={{ id: 7, name: 'edge-east' }} />);
    expect(mockUseTargetConnectivity).toHaveBeenCalledWith('cluster', 7);
  });

  it('renders cluster name when showLabel is true', () => {
    setRegistryState({ state: 'reachable' });
    render(
      <ClusterStatusBadge cluster={{ id: 1, name: 'edge-east' }} showLabel />,
    );
    expect(screen.getByText('edge-east')).toBeInTheDocument();
  });
});
