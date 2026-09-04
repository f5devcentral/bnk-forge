/**
 * Tests for HeroOmniSearch component
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { HeroOmniSearch } from '../HeroOmniSearch';
import type { Project } from '@/types/project';
import type { KubernetesCluster } from '@/types/k8s';

// Mock navigation
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const mockProjects: Project[] = [
  {
    id: 1,
    name: 'ecommerce-api',
    description: 'E-commerce microservices',
    cloud_provider: 'aws',
    region: 'us-east-1',
    module_count: 5,
    deployed_count: 5,
    failed_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'azure-core',
    description: 'Azure payments backend',
    cloud_provider: 'azure',
    region: 'australiaeast',
    module_count: 3,
    deployed_count: 3,
    failed_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

const mockClusters: KubernetesCluster[] = [
  {
    id: 1,
    name: 'eks-prod-us-east',
    cloud_provider: 'aws',
    region: 'us-east-1',
    status: 'active',
    node_count: 8,
    detected_platform_profile: 'EKS',
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'aks-sydney-stage',
    cloud_provider: 'azure',
    region: 'australiaeast',
    status: 'active',
    node_count: 4,
    detected_platform_profile: 'AKS',
    created_at: '2026-01-01T00:00:00Z',
  },
];

describe('HeroOmniSearch', () => {
  it('renders search input and category filter chips', () => {
    render(<HeroOmniSearch projects={mockProjects} clusters={mockClusters} />);

    expect(screen.getByPlaceholderText(/search fqdn/i)).toBeInTheDocument();
    expect(screen.getByText('All')).toBeInTheDocument();
    expect(screen.getByText('Ingresses & VIPs')).toBeInTheDocument();
    expect(screen.getByText('AWS')).toBeInTheDocument();
    expect(screen.getByText('Azure')).toBeInTheDocument();
    expect(screen.getByText('GKE')).toBeInTheDocument();
    expect(screen.getByText('Bare Metal')).toBeInTheDocument();
  });

  it('filters results and shows live search matches on typing', async () => {
    const user = userEvent.setup();
    render(<HeroOmniSearch projects={mockProjects} clusters={mockClusters} debounceMs={0} />);

    const input = screen.getByPlaceholderText(/search fqdn/i);
    await user.type(input, 'api.example.com');

    await waitFor(() => {
      // Should show the mock ingress search result
      expect(screen.getByText('api.example.com')).toBeInTheDocument();
      expect(screen.getByText(/prod-ingress/i)).toBeInTheDocument();
      expect(screen.getByText(/api-svc:8080/i)).toBeInTheDocument();
    });
  });

  it('allows clicking category chips to filter provider scope', async () => {
    const user = userEvent.setup();
    render(<HeroOmniSearch projects={mockProjects} clusters={mockClusters} />);

    const azureChip = screen.getByText('Azure');
    await user.click(azureChip);

    const input = screen.getByPlaceholderText(/search fqdn/i);
    await user.type(input, 'azure');

    await waitFor(() => {
      expect(screen.getByText('azure-core')).toBeInTheDocument();
    }, { timeout: 4000 });
  });

  it('navigates when a search result item is clicked', async () => {
    const user = userEvent.setup();
    render(<HeroOmniSearch projects={mockProjects} clusters={mockClusters} />);

    const input = screen.getByPlaceholderText(/search fqdn/i);
    await user.type(input, 'ecommerce');

    await waitFor(() => {
      expect(screen.getByText('ecommerce-api')).toBeInTheDocument();
    });

    await user.click(screen.getByText('ecommerce-api'));
    expect(mockNavigate).toHaveBeenCalledWith('/projects/1');
  });
});
