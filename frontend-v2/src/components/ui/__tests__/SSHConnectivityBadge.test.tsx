/**
 * Tests for SSHConnectivityBadge.
 *
 * Covers:
 *   - non-operator (viewer) role: probe is gated off (enabled=false forwarded
 *     to useSSHConnectivity) and the badge renders its muted/unknown state,
 *     not the destructive "connection check failed" state.
 *   - operator/admin role: probe fires as before (enabled=true forwarded).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@/test/test-utils';
import { SSHConnectivityBadge } from '../SSHConnectivityBadge';

vi.mock('@/hooks/useSSHConnectivity', () => ({
  useSSHConnectivity: vi.fn(),
}));
vi.mock('@/hooks/useRole', () => ({
  useRole: vi.fn(),
}));

import { useSSHConnectivity } from '@/hooks/useSSHConnectivity';
import { useRole } from '@/hooks/useRole';

const mockUseSSHConnectivity = vi.mocked(useSSHConnectivity);
const mockUseRole = vi.mocked(useRole);

function setRole(isOperator: boolean) {
  mockUseRole.mockReturnValue({
    role: isOperator ? 'operator' : 'viewer',
    isAdmin: false,
    isOperator,
    isViewer: true,
    hasRole: vi.fn(),
    hasMinRole: vi.fn(),
    roleLabel: isOperator ? 'Operator' : 'Viewer',
  });
}

function makeQueryReturn(overrides: Partial<ReturnType<typeof useSSHConnectivity>> = {}) {
  return {
    isFetching: false,
    isError: false,
    error: null,
    data: undefined,
    ...overrides,
  } as ReturnType<typeof useSSHConnectivity>;
}

describe('SSHConnectivityBadge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('viewer (non-operator): forwards enabled=false and renders muted state, not destructive', () => {
    setRole(false);
    mockUseSSHConnectivity.mockReturnValue(makeQueryReturn());

    render(<SSHConnectivityBadge credentialId={1} label="mgx-1" />);

    expect(mockUseSSHConnectivity).toHaveBeenCalledWith(1, false);

    const el = document.querySelector('span[title], div[title]');
    expect(el).toBeTruthy();
    expect(el).toHaveAttribute('title', 'Jumphost: mgx-1');

    const icon = document.querySelector('svg');
    expect(icon).toHaveClass('text-muted-foreground');
    expect(icon).not.toHaveClass('text-destructive');
  });

  it('operator: forwards enabled=true so the probe fires as before', () => {
    setRole(true);
    mockUseSSHConnectivity.mockReturnValue(makeQueryReturn());

    render(<SSHConnectivityBadge credentialId={1} label="mgx-1" />);

    expect(mockUseSSHConnectivity).toHaveBeenCalledWith(1, true);
  });

  it('operator with a failed probe still renders the destructive state', () => {
    setRole(true);
    mockUseSSHConnectivity.mockReturnValue(
      makeQueryReturn({ isError: true, error: new Error('Requires role: admin or operator') }),
    );

    render(<SSHConnectivityBadge credentialId={1} label="mgx-1" />);

    const icon = document.querySelector('svg');
    expect(icon).toHaveClass('text-destructive');
  });
});
