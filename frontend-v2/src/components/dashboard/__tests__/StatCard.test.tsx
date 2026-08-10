/**
 * Tests for StatCard component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { Activity, AlertTriangle } from 'lucide-react';
import { StatCard } from '../StatCard';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('StatCard', () => {
  it('renders label and value', () => {
    render(<StatCard label="Total Projects" value={12} icon={Activity} />);
    expect(screen.getByText('Total Projects')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
  });

  it('renders string value', () => {
    render(<StatCard label="Status" value="Healthy" icon={Activity} />);
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Healthy')).toBeInTheDocument();
  });

  it('applies variant styling for danger', () => {
    const { container } = render(
      <StatCard label="Errors" value={3} icon={AlertTriangle} variant="danger" />
    );
    expect(screen.getByText('Errors')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    // D-020: danger variant now uses the destructive token instead of `red-*` classes.
    const card = container.firstChild as HTMLElement;
    expect(card.className).toMatch(/destructive/);
  });
});
