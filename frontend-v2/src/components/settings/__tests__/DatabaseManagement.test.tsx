/**
 * Tests for DatabaseManagement component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import DatabaseManagement from '../DatabaseManagement';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('DatabaseManagement', () => {
  it('renders collapsible card header', () => {
    render(<DatabaseManagement />);
    expect(screen.getByText('Database management')).toBeInTheDocument();
    expect(
      screen.getByText('Monitor database size, cleanup old records, and optimize performance')
    ).toBeInTheDocument();
  });

  it('shows database stats when expanded', async () => {
    const user = userEvent.setup();
    render(<DatabaseManagement />);

    await user.click(screen.getByText('Database management'));

    await waitFor(() => {
      expect(screen.getByText('Database Overview')).toBeInTheDocument();
      expect(screen.getByText('Total Database Size')).toBeInTheDocument();
    });
  });

  it('shows cleanup tools when expanded', async () => {
    const user = userEvent.setup();
    render(<DatabaseManagement />);

    await user.click(screen.getByText('Database management'));

    await waitFor(() => {
      expect(screen.getByText('Cleanup Tools')).toBeInTheDocument();
      expect(screen.getByText('Delete Old Records')).toBeInTheDocument();
      expect(screen.getByText('Vacuum & Optimize')).toBeInTheDocument();
    });
  });
});
