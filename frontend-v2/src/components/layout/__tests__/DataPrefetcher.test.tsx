import { describe, it, expect, vi } from 'vitest';
import { render } from '@/test/test-utils';
import { DataPrefetcher } from '../DataPrefetcher';

// Mock the API module
vi.mock('@/lib/api', () => ({
  api: {
    getProjects: vi.fn().mockResolvedValue({ projects: [] }),
    getK8sResourceTypes: vi.fn().mockResolvedValue([]),
    getAllClusters: vi.fn().mockResolvedValue({ clusters: [] }),
    getProjectClusters: vi.fn().mockResolvedValue({ clusters: [] }),
    listHelmRepositories: vi.fn().mockResolvedValue([]),
  },
}));

describe('DataPrefetcher', () => {
  it('renders nothing (returns null)', () => {
    const { container } = render(<DataPrefetcher />);
    expect(container.innerHTML).toBe('');
  });

  it('calls prefetchQuery for core data on mount', async () => {
    const { api } = await import('@/lib/api');

    render(<DataPrefetcher />);

    // The component fires prefetchQuery calls via useEffect.
    // Since QueryClient.prefetchQuery calls the queryFn, we verify it was called.
    // Wait a tick for the useEffect to fire
    await vi.waitFor(() => {
      expect(api.getProjects).toHaveBeenCalled();
    });
  });
});
