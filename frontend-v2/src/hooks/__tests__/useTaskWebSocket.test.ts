/**
 * Tests for useTaskWebSocket hook
 *
 * Covers: WebSocket connection, message handling, task cache updates,
 * reconnection logic, and cleanup.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useTaskWebSocket } from '@/hooks/useTaskWebSocket';
import React from 'react';

// ========================================================================
// Mock WebSocket as a proper class so `new WebSocket(url)` works
// ========================================================================

let mockWsInstances: MockWebSocket[] = [];

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();
  send = vi.fn();

  constructor(url: string) {
    this.url = url;
    mockWsInstances.push(this);
  }

  // Helpers for tests
  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    if (this.onopen) this.onopen(new Event('open'));
  }

  simulateMessage(data: unknown) {
    if (this.onmessage) {
      this.onmessage(new MessageEvent('message', { data: JSON.stringify(data) }));
    }
  }

  simulateMessageRaw(data: string) {
    if (this.onmessage) {
      this.onmessage(new MessageEvent('message', { data }));
    }
  }

  simulateClose() {
    this.readyState = MockWebSocket.CLOSED;
    if (this.onclose) this.onclose(new CloseEvent('close'));
  }

  simulateError() {
    if (this.onerror) this.onerror(new Event('error'));
  }
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function latestWs(): MockWebSocket {
  return mockWsInstances[mockWsInstances.length - 1];
}

describe('useTaskWebSocket', () => {
  beforeEach(() => {
    mockWsInstances = [];
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.stubGlobal('WebSocket', MockWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('connects to WebSocket on mount', () => {
    renderHook(() => useTaskWebSocket(), { wrapper: createWrapper() });

    act(() => {
      vi.runOnlyPendingTimers();
    });

    expect(mockWsInstances).toHaveLength(1);
    expect(latestWs().url).toContain('ws');
    expect(latestWs().url).toContain('/api/tasks/ws/tasks');
  });

  it('sets isConnected to true on open and sends ping', async () => {
    const { result } = renderHook(() => useTaskWebSocket(), { wrapper: createWrapper() });

    act(() => {
      vi.runOnlyPendingTimers();
    });

    expect(result.current.isConnected).toBe(false);

    act(() => {
      latestWs().simulateOpen();
    });

    await waitFor(() => {
      expect(result.current.isConnected).toBe(true);
    });

    expect(latestWs().send).toHaveBeenCalledWith('ping');
  });

  it('handles task_update messages and updates query cache', async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0, staleTime: 0 },
        mutations: { retry: false },
      },
    });

    queryClient.setDefaultOptions({
      queries: {
        retry: false,
        gcTime: Infinity,
        staleTime: 0,
      },
      mutations: { retry: false },
    });

    // Seed cache with a task
    queryClient.setQueryData(['tasks', 42], {
      id: 42,
      status: 'in_progress',
      exit_code: null,
      error: null,
      duration_seconds: null,
    });

    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useTaskWebSocket(), { wrapper });

    act(() => {
      vi.runOnlyPendingTimers();
    });

    act(() => {
      latestWs().simulateOpen();
    });

    act(() => {
      latestWs().simulateMessage({
        type: 'task_update',
        task_id: 42,
        status: 'completed',
        exit_code: 0,
        duration_seconds: 15,
      });
    });

    const updatedTask = queryClient.getQueryData(['tasks', 42]) as Record<string, unknown>;
    expect(updatedTask.status).toBe('completed');
    expect(updatedTask.exit_code).toBe(0);
    expect(updatedTask.duration_seconds).toBe(15);
  });

  it('responds to server ping messages', () => {
    renderHook(() => useTaskWebSocket(), { wrapper: createWrapper() });

    act(() => {
      vi.runOnlyPendingTimers();
    });

    act(() => {
      latestWs().simulateOpen();
    });

    // Clear initial ping from onopen
    latestWs().send.mockClear();

    act(() => {
      latestWs().simulateMessage({ type: 'ping' });
    });

    expect(latestWs().send).toHaveBeenCalledWith('ping');
  });

  it('sets isConnected to false on close and attempts reconnect', async () => {
    const { result } = renderHook(() => useTaskWebSocket(), { wrapper: createWrapper() });

    act(() => {
      vi.runOnlyPendingTimers();
    });

    act(() => {
      latestWs().simulateOpen();
    });

    await waitFor(() => {
      expect(result.current.isConnected).toBe(true);
    });

    act(() => {
      latestWs().simulateClose();
    });

    expect(result.current.isConnected).toBe(false);

    // Advance timer past the first reconnect delay (1000ms * 2^0 = 1000ms)
    act(() => {
      vi.advanceTimersByTime(1500);
    });

    // Should have created a second WebSocket instance (reconnect)
    expect(mockWsInstances).toHaveLength(2);
  });

  it('cleans up WebSocket on unmount', () => {
    const { unmount } = renderHook(() => useTaskWebSocket(), { wrapper: createWrapper() });

    act(() => {
      vi.runOnlyPendingTimers();
    });

    act(() => {
      latestWs().simulateOpen();
    });

    const ws = latestWs();

    unmount();

    expect(ws.close).toHaveBeenCalled();
  });

  it('ignores malformed messages gracefully', () => {
    renderHook(() => useTaskWebSocket(), { wrapper: createWrapper() });

    act(() => {
      vi.runOnlyPendingTimers();
    });

    act(() => {
      latestWs().simulateOpen();
    });

    // Send non-JSON data - should not throw
    expect(() => {
      act(() => {
        latestWs().simulateMessageRaw('not valid json');
      });
    }).not.toThrow();
  });
});
