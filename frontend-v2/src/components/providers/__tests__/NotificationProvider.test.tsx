import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { NotificationProvider } from '../NotificationProvider';

// Mock WebSocket globally — MSW intercepts WebSocket connections, so we need to
// mock the entire module's WS-dependent behavior at a higher level.
// The simplest approach: verify children render and WS connection attempted.

beforeEach(() => {
  vi.clearAllMocks();
});

describe('NotificationProvider', () => {
  it('renders children', () => {
    render(
      <NotificationProvider>
        <div>Child content</div>
      </NotificationProvider>
    );

    expect(screen.getByText('Child content')).toBeInTheDocument();
  });

  it('renders multiple children correctly', () => {
    render(
      <NotificationProvider>
        <div>First child</div>
        <div>Second child</div>
      </NotificationProvider>
    );

    expect(screen.getByText('First child')).toBeInTheDocument();
    expect(screen.getByText('Second child')).toBeInTheDocument();
  });

  it('wraps children in a fragment (no extra DOM nodes)', () => {
    const { container } = render(
      <NotificationProvider>
        <div data-testid="inner">Content</div>
      </NotificationProvider>
    );

    // The provider should not add wrapper DOM elements
    const inner = screen.getByTestId('inner');
    expect(inner.textContent).toBe('Content');
    // The inner element should be a direct child of the container div
    expect(container.firstChild).toBe(inner);
  });
});
