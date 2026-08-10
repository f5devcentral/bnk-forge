/**
 * Tests for K8sSidebar component.
 *
 * D-020: the sidebar is now a quiet category-tree navigator only — the
 * Cluster Scan / Export Config buttons and the unhealthy/namespace toggles
 * moved to the page toolbar (ResourcePageHeader actions).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { K8sSidebar } from '../K8sSidebar';

// useCrds is called inside K8sSidebar; mock it so these component tests stay fast.
vi.mock('@/hooks/useCrds', () => ({
  useCrds: () => ({ data: undefined, isLoading: false }),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

const defaultProps = {
  clusterId: 1,
  selectedResourceType: 'pod',
  onSelectResourceType: vi.fn(),
  expandedCategories: ['Workloads'],
  onToggleCategory: vi.fn(),
  filteredResourceCount: 5,
  showClusterScan: false,
};

describe('K8sSidebar', () => {
  it('renders resource type items when category is expanded', () => {
    render(<K8sSidebar {...defaultProps} />);
    // Workloads category is expanded, should show items like Pods, Deployments
    expect(screen.getByText('Pods')).toBeInTheDocument();
    expect(screen.getByText('Deployments')).toBeInTheDocument();
  });

  it('calls onSelectResourceType when a resource type is clicked', async () => {
    const user = userEvent.setup();
    const onSelectResourceType = vi.fn();
    render(<K8sSidebar {...defaultProps} onSelectResourceType={onSelectResourceType} />);

    await user.click(screen.getByText('Deployments'));
    expect(onSelectResourceType).toHaveBeenCalled();
  });

  it('calls onToggleCategory when a category header is clicked', async () => {
    const user = userEvent.setup();
    const onToggleCategory = vi.fn();
    render(<K8sSidebar {...defaultProps} onToggleCategory={onToggleCategory} />);

    await user.click(screen.getByText('Workloads'));
    expect(onToggleCategory).toHaveBeenCalledWith('Workloads');
  });

  it('hides items when their category is collapsed', () => {
    render(<K8sSidebar {...defaultProps} expandedCategories={[]} />);
    expect(screen.queryByText('Pods')).not.toBeInTheDocument();
  });
});
