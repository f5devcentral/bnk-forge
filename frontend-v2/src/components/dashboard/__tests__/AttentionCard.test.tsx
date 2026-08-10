/**
 * Tests for AttentionCard component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { AttentionCard, type AttentionItem } from '../AttentionCard';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AttentionCard', () => {
  const failureItem: AttentionItem = {
    id: 1,
    type: 'failure',
    project: 'Production',
    module: 'bnk-vpc',
    message: 'Apply failed: timeout waiting for resource',
    projectId: 1,
  };

  const driftItem: AttentionItem = {
    id: 2,
    type: 'drift',
    project: 'Staging',
    module: 'bnk-eks',
    message: 'Configuration drift detected in security group',
    projectId: 2,
  };

  it('renders failure item with error styling', () => {
    render(<AttentionCard item={failureItem} />);
    expect(screen.getByText('bnk-vpc')).toBeInTheDocument();
    expect(screen.getByText('Production')).toBeInTheDocument();
    expect(screen.getByText('Apply failed: timeout waiting for resource')).toBeInTheDocument();
    expect(screen.getByText('View Error')).toBeInTheDocument();
  });

  it('renders drift item with warning styling', () => {
    render(<AttentionCard item={driftItem} />);
    expect(screen.getByText('bnk-eks')).toBeInTheDocument();
    expect(screen.getByText('Staging')).toBeInTheDocument();
    expect(screen.getByText('Review Drift')).toBeInTheDocument();
  });

  it('links to project detail page', () => {
    render(<AttentionCard item={failureItem} />);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/projects/1');
  });
});
