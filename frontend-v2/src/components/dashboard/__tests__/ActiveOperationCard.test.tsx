/**
 * Tests for ActiveOperationCard component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { ActiveOperationCard } from '../ActiveOperationCard';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ActiveOperationCard', () => {
  const defaultProps = {
    projectName: 'Production',
    moduleName: 'bnk-vpc',
    taskType: 'apply',
    projectId: 1,
    time: '2m ago',
  };

  it('renders operation details', () => {
    render(<ActiveOperationCard {...defaultProps} />);
    expect(screen.getByText('Apply')).toBeInTheDocument();
    expect(screen.getByText('bnk-vpc')).toBeInTheDocument();
    expect(screen.getByText('Production')).toBeInTheDocument();
    expect(screen.getByText('2m ago')).toBeInTheDocument();
  });

  it('links to project detail page', () => {
    render(<ActiveOperationCard {...defaultProps} />);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/projects/1');
  });

  it('capitalizes task type', () => {
    render(<ActiveOperationCard {...defaultProps} taskType="destroy" />);
    expect(screen.getByText('Destroy')).toBeInTheDocument();
  });
});
