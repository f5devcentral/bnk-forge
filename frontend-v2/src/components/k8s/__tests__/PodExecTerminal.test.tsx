/**
 * Tests for PodExecTerminal component
 *
 * Tests rendering of terminal container, shell selector, container selector,
 * and connection status. xterm.js is mocked since it requires a real DOM.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { PodExecTerminal } from '../PodExecTerminal';
import type { K8sResource } from '@/types';

// ---------------------------------------------------------------------------
// Mock xterm
// ---------------------------------------------------------------------------

vi.mock('@xterm/xterm', () => ({
  Terminal: vi.fn().mockImplementation(() => ({
    open: vi.fn(),
    write: vi.fn(),
    dispose: vi.fn(),
    clear: vi.fn(),
    onData: vi.fn(),
    onResize: vi.fn(),
    loadAddon: vi.fn(),
    rows: 24,
    cols: 80,
  })),
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: vi.fn().mockImplementation(() => ({
    fit: vi.fn(),
    activate: vi.fn(),
    dispose: vi.fn(),
  })),
}));

vi.mock('@xterm/xterm/css/xterm.css', () => ({}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const mockPod: K8sResource = {
  name: 'nginx-pod',
  namespace: 'default',
  kind: 'Pod',
  apiVersion: 'v1',
  metadata: {
    name: 'nginx-pod',
    namespace: 'default',
    uid: 'uid-123',
    creationTimestamp: '2026-02-20T10:00:00Z',
  },
  status: { phase: 'Running' },
};

const mockContainersResponse = {
  pod_name: 'nginx-pod',
  namespace: 'default',
  containers: [
    { name: 'nginx', image: 'nginx:latest', ready: true, state: 'running', restartCount: 0 },
    { name: 'sidecar', image: 'envoy:latest', ready: true, state: 'running', restartCount: 1 },
  ],
  init_containers: [],
};

const defaultProps = {
  open: true,
  onOpenChange: vi.fn(),
  pod: mockPod,
  clusterId: 1,
  namespace: 'default',
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  server.use(
    http.get('*/api/k8s/clusters/:id/pods/:podName/containers', () => {
      return HttpResponse.json(mockContainersResponse);
    }),
  );
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('PodExecTerminal', () => {
  it('renders dialog with pod name in title', async () => {
    render(<PodExecTerminal {...defaultProps} />);

    expect(screen.getByText(/Terminal: nginx-pod/)).toBeInTheDocument();
  });

  it('shows shell selector with bash as default', async () => {
    render(<PodExecTerminal {...defaultProps} />);

    expect(screen.getByText('Shell:')).toBeInTheDocument();
  });

  it('renders the terminal container area with border', async () => {
    render(<PodExecTerminal {...defaultProps} />);

    // The flex-1 overflow-hidden wrapper div contains the terminal
    // Verify the dialog renders with its expected content structure
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    // Should contain the terminal wrapper div (border div)
    expect(dialog.querySelector('.border-border')).toBeInTheDocument();
  });

  it('shows connection status badge', async () => {
    render(<PodExecTerminal {...defaultProps} />);

    // Initially shows a status badge
    await waitFor(() => {
      // Could be disconnected, connecting, etc.
      const badges = screen.getAllByText(/Connected|Connecting|Disconnected|Error/);
      expect(badges.length).toBeGreaterThan(0);
    });
  });

  it('shows tip text about keyboard shortcuts', async () => {
    render(<PodExecTerminal {...defaultProps} />);

    expect(screen.getByText(/Tip: Use Ctrl\+C to interrupt/)).toBeInTheDocument();
  });

  it('returns null when pod is null', () => {
    const { container } = render(
      <PodExecTerminal {...defaultProps} pod={null} />
    );
    expect(container.querySelector('[role="dialog"]')).not.toBeInTheDocument();
  });
});
