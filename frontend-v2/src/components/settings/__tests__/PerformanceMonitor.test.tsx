/**
 * Tests for PerformanceMonitor component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import PerformanceMonitor from '../PerformanceMonitor';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PerformanceMonitor', () => {
  it('renders system performance heading', () => {
    render(<PerformanceMonitor />);
    expect(screen.getByText('Performance monitor')).toBeInTheDocument();
  });

  it('displays service health section after loading', async () => {
    render(<PerformanceMonitor />);

    await waitFor(() => {
      expect(screen.getByText('Service Health')).toBeInTheDocument();
    });
  });

  it('shows refresh button', () => {
    render(<PerformanceMonitor />);
    expect(screen.getByText('Refresh')).toBeInTheDocument();
  });
});
