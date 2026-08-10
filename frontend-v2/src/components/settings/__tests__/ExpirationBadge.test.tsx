/**
 * Tests for ExpirationBadge component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { ExpirationBadge } from '../ExpirationBadge';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ExpirationBadge', () => {
  it('returns null when no expiryDate is provided', () => {
    const { container } = render(<ExpirationBadge />);
    expect(container.innerHTML).toBe('');
  });

  it('shows Expired badge for past dates', () => {
    const pastDate = new Date(Date.now() - 86400000).toISOString(); // 1 day ago
    render(<ExpirationBadge expiryDate={pastDate} />);
    expect(screen.getByText('Expired')).toBeInTheDocument();
  });

  it('shows days remaining for dates more than 24 hours away', () => {
    const futureDate = new Date(Date.now() + 3 * 86400000).toISOString(); // 3 days from now
    render(<ExpirationBadge expiryDate={futureDate} />);
    // Should show "3d left" or "2d left" depending on exact timing
    expect(screen.getByText(/\dd left/)).toBeInTheDocument();
  });
});
