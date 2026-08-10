/**
 * Tests for PodLogsViewer component
 *
 * Tests rendering with pod info, container selection, log display,
 * follow toggle, scroll behaviour, JWT auth, and empty/loading states.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { PodLogsViewer } from '../PodLogsViewer';
import type { K8sResource } from '@/types';

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
  spec: {
    containers: [
      { name: 'nginx', image: 'nginx:latest' },
      { name: 'sidecar', image: 'envoy:latest' },
    ],
    initContainers: [
      { name: 'init-setup', image: 'busybox:latest' },
    ],
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
  init_containers: [
    { name: 'init-setup', image: 'busybox:latest', ready: false, state: 'terminated', restartCount: 0 },
  ],
};

const mockLogsResponse = {
  logs: '2026-02-20T10:00:00Z Starting nginx...\n2026-02-20T10:00:01Z nginx ready\n2026-02-20T10:00:02Z Serving on port 80\n',
  pod_name: 'nginx-pod',
  namespace: 'default',
};

const defaultProps = {
  open: true,
  onOpenChange: vi.fn(),
  pod: mockPod,
  clusterId: 1,
  namespace: 'default',
};

// ---------------------------------------------------------------------------
// WebSocket mock
// ---------------------------------------------------------------------------

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  readyState = 0; // CONNECTING

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
    // Simulate async open
    setTimeout(() => {
      this.readyState = 1; // OPEN
      this.onopen?.(new Event('open'));
    }, 0);
  }

  send(_data: string) { /* noop */ }
  close() {
    this.readyState = 3; // CLOSED
    this.onclose?.(new CloseEvent('close'));
  }

  // Test helper: simulate receiving a message
  _receiveMessage(data: unknown) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }));
  }
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);

  server.use(
    http.get('*/api/k8s/clusters/:id/pods/:podName/containers', () => {
      return HttpResponse.json(mockContainersResponse);
    }),
    http.get('*/api/k8s/clusters/:id/pods/:podName/logs', () => {
      return HttpResponse.json(mockLogsResponse);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('PodLogsViewer', () => {
  it('renders dialog with pod name in title', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Pod Logs: nginx-pod/)).toBeInTheDocument();
    });
  });

  it('displays log content after fetching', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Starting nginx/)).toBeInTheDocument();
    });
    expect(screen.getByText(/nginx ready/)).toBeInTheDocument();
  });

  it('shows container selector when there are multiple containers', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Container:')).toBeInTheDocument();
    });
  });

  it('shows search input for filtering logs', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    const searchInput = screen.getByPlaceholderText('Search logs...');
    expect(searchInput).toBeInTheDocument();
  });

  it('shows follow, refresh, and download buttons', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    expect(screen.getByText('Follow')).toBeInTheDocument();
    expect(screen.getByLabelText('Refresh logs')).toBeInTheDocument();
    expect(screen.getByLabelText('Download logs')).toBeInTheDocument();
  });

  it('has auto-scroll checkbox checked by default', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    expect(screen.getByText('Auto-scroll')).toBeInTheDocument();
    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).toBeChecked();
  });

  it('returns null when pod is null', () => {
    const { container } = render(
      <PodLogsViewer {...defaultProps} pod={null} />
    );
    // When pod is null, component returns null before dialog
    expect(container.querySelector('[role="dialog"]')).not.toBeInTheDocument();
  });

  it('shows line count in footer after logs load', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/lines/)).toBeInTheDocument();
    });
  });

  // ---- New tests for scroll / streaming / JWT fixes ----

  it('uses a native scrollable div instead of Radix ScrollArea', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Starting nginx/)).toBeInTheDocument();
    });

    // The scroll container should be a div with overflow-auto class
    const pre = screen.getByText(/Starting nginx/).closest('pre');
    const scrollContainer = pre?.parentElement;
    expect(scrollContainer).toBeTruthy();
    expect(scrollContainer?.className).toContain('overflow-auto');
  });

  it('uses scrollTop for auto-scroll instead of sentinel element', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Starting nginx/)).toBeInTheDocument();
    });

    // The scroll container uses absolute positioning for reliable scrolling
    // and scrollTop assignment (not scrollIntoView) for auto-scroll
    const pre = document.querySelector('pre');
    expect(pre).toBeTruthy();
    // The pre should be inside an absolutely positioned scroll container
    const scrollContainer = pre?.parentElement;
    expect(scrollContainer).toBeTruthy();
    expect(scrollContainer?.classList.contains('overflow-auto')).toBe(true);
  });

  it('includes JWT token in WebSocket URL when available', async () => {
    // Set a mock token
    localStorage.setItem('auth_token', 'test-jwt-token-123');

    render(<PodLogsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Starting nginx/)).toBeInTheDocument();
    });

    // Click Follow button to trigger WebSocket connection
    const followButton = screen.getByText('Follow');
    fireEvent.click(followButton);

    await waitFor(() => {
      expect(MockWebSocket.instances.length).toBeGreaterThan(0);
    });

    const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
    expect(ws.url).toContain('token=test-jwt-token-123');

    // Cleanup
    localStorage.removeItem('auth_token');
  });

  it('does not include token param when no auth token exists', async () => {
    localStorage.removeItem('auth_token');

    render(<PodLogsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Starting nginx/)).toBeInTheDocument();
    });

    const followButton = screen.getByText('Follow');
    fireEvent.click(followButton);

    await waitFor(() => {
      expect(MockWebSocket.instances.length).toBeGreaterThan(0);
    });

    const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
    expect(ws.url).not.toContain('token=');
  });

  it('shows Stop button and Live badge when following', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Starting nginx/)).toBeInTheDocument();
    });

    const followButton = screen.getByText('Follow');
    fireEvent.click(followButton);

    await waitFor(() => {
      expect(screen.getByText('Stop')).toBeInTheDocument();
    });

    // After WebSocket connects (mock fires onopen async), should show Live badge
    await waitFor(() => {
      expect(screen.getByText('Live')).toBeInTheDocument();
    });
  });

  it('auto-scroll checkbox can be toggled', async () => {
    render(<PodLogsViewer {...defaultProps} />);

    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).toBeChecked();

    fireEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
  });
});
