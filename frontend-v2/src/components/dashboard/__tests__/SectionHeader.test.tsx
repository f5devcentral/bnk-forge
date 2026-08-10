/**
 * Tests for SectionHeader component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { Activity } from 'lucide-react';
import { SectionHeader } from '../SectionHeader';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SectionHeader', () => {
  it('renders title and icon', () => {
    render(<SectionHeader icon={Activity} title="Active Operations" />);
    expect(screen.getByText('Active Operations')).toBeInTheDocument();
  });

  it('renders count badge when provided', () => {
    render(<SectionHeader icon={Activity} title="Operations" count={5} />);
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('renders view all link when href is provided', () => {
    render(
      <SectionHeader
        icon={Activity}
        title="Tasks"
        viewAllHref="/tasks"
        viewAllLabel="See All Tasks"
      />
    );
    const link = screen.getByText('See All Tasks');
    expect(link).toBeInTheDocument();
    expect(link.closest('a')).toHaveAttribute('href', '/tasks');
  });
});
