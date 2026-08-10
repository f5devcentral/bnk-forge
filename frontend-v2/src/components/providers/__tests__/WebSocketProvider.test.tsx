import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { WebSocketProvider } from '../WebSocketProvider';

// Mock the hook to control isConnected state
vi.mock('@/hooks/useTaskWebSocket', () => ({
  useTaskWebSocket: vi.fn(() => ({ isConnected: true })),
}));

import { useTaskWebSocket } from '@/hooks/useTaskWebSocket';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('WebSocketProvider', () => {
  it('renders children and hides reconnecting indicator when connected', () => {
    vi.mocked(useTaskWebSocket).mockReturnValue({ isConnected: true });

    render(
      <WebSocketProvider>
        <div>App Content</div>
      </WebSocketProvider>
    );

    expect(screen.getByText('App Content')).toBeInTheDocument();
    expect(screen.queryByText('Reconnecting...')).not.toBeInTheDocument();
  });
});
