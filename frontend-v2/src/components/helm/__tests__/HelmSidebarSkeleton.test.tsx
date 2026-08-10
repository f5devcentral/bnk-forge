/**
 * Tests for HelmSidebarSkeleton component
 */
import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { HelmSidebarSkeleton } from '@/components/helm/HelmSidebarSkeleton';

describe('HelmSidebarSkeleton', () => {
  it('renders Releases section header', () => {
    render(<HelmSidebarSkeleton />);

    expect(screen.getByText('Releases')).toBeInTheDocument();
  });

  it('renders Charts section header', () => {
    render(<HelmSidebarSkeleton />);

    expect(screen.getByText('Charts')).toBeInTheDocument();
  });

  it('renders animated pulse skeleton elements', () => {
    const { container } = render(<HelmSidebarSkeleton />);

    const pulsingElements = container.querySelectorAll('.animate-pulse');
    expect(pulsingElements.length).toBeGreaterThan(5);
  });
});
