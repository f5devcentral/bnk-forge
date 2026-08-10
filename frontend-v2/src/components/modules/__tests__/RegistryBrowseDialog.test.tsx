/**
 * Tests for RegistryBrowseDialog component
 *
 * Covers: renders search interface, minimum character prompt,
 * displays results, provider filter, import button.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { RegistryBrowseDialog } from '../RegistryBrowseDialog';

const mockRegistryResults = {
  modules: [
    {
      id: 'hashicorp/consul/aws',
      namespace: 'hashicorp',
      name: 'consul',
      provider: 'aws',
      description: 'HashiCorp Consul cluster on AWS',
      source: 'https://github.com/hashicorp/terraform-aws-consul',
      published_at: '2026-01-15T00:00:00Z',
      downloads: 150000,
      verified: true,
      version: '0.11.0',
    },
    {
      id: 'terraform-aws-modules/vpc/aws',
      namespace: 'terraform-aws-modules',
      name: 'vpc',
      provider: 'aws',
      description: 'Terraform module to create AWS VPC resources',
      source: 'https://github.com/terraform-aws-modules/terraform-aws-vpc',
      published_at: '2026-02-01T00:00:00Z',
      downloads: 5000000,
      verified: true,
      version: '5.5.0',
    },
  ],
  meta: { limit: 50, offset: 0, total_count: 2 },
  source: 'opentofu' as const,
};

describe('RegistryBrowseDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders dialog with title and search input', () => {
    render(<RegistryBrowseDialog {...defaultProps} />);

    expect(screen.getByText('Browse OpenTofu Registry')).toBeInTheDocument();
    expect(screen.getByLabelText(/search module registry/i)).toBeInTheDocument();
  });

  it('shows minimum characters prompt when search is empty', () => {
    render(<RegistryBrowseDialog {...defaultProps} />);

    expect(screen.getByText(/enter at least 3 characters to search/i)).toBeInTheDocument();
  });

  it('shows provider filter dropdown', () => {
    render(<RegistryBrowseDialog {...defaultProps} />);

    // Should see the provider filter
    expect(screen.getByText('All Providers')).toBeInTheDocument();
  });

  it('shows verified filter dropdown', () => {
    render(<RegistryBrowseDialog {...defaultProps} />);

    expect(screen.getByText('All Modules')).toBeInTheDocument();
  });

  it('displays search results with module details', async () => {
    server.use(
      http.get('*/api/registry/search', () => {
        return HttpResponse.json(mockRegistryResults);
      })
    );

    const user = userEvent.setup();
    render(<RegistryBrowseDialog {...defaultProps} />);

    // Type a search query (min 3 chars)
    const searchInput = screen.getByLabelText(/search module registry/i);
    await user.type(searchInput, 'consul');

    // Wait for debounced search results
    await waitFor(
      () => {
        expect(screen.getByText('hashicorp/consul')).toBeInTheDocument();
      },
      { timeout: 3000 }
    );

    // Check module details
    expect(screen.getByText('terraform-aws-modules/vpc')).toBeInTheDocument();
  });

  it('shows verified badge for verified modules', async () => {
    server.use(
      http.get('*/api/registry/search', () => {
        return HttpResponse.json(mockRegistryResults);
      })
    );

    const user = userEvent.setup();
    render(<RegistryBrowseDialog {...defaultProps} />);

    const searchInput = screen.getByLabelText(/search module registry/i);
    await user.type(searchInput, 'consul');

    await waitFor(
      () => {
        const verifiedBadges = screen.getAllByText('Verified');
        expect(verifiedBadges.length).toBeGreaterThanOrEqual(1);
      },
      { timeout: 3000 }
    );
  });

  it('shows Import buttons for each result', async () => {
    server.use(
      http.get('*/api/registry/search', () => {
        return HttpResponse.json(mockRegistryResults);
      })
    );

    const user = userEvent.setup();
    render(<RegistryBrowseDialog {...defaultProps} />);

    const searchInput = screen.getByLabelText(/search module registry/i);
    await user.type(searchInput, 'consul');

    await waitFor(
      () => {
        const importButtons = screen.getAllByRole('button', { name: /import/i });
        expect(importButtons.length).toBeGreaterThanOrEqual(2);
      },
      { timeout: 3000 }
    );
  });

  it('shows "no modules found" when search returns empty results', async () => {
    server.use(
      http.get('*/api/registry/search', () => {
        return HttpResponse.json({
          modules: [],
          meta: { limit: 50, offset: 0, total_count: 0 },
          source: 'opentofu',
        });
      })
    );

    const user = userEvent.setup();
    render(<RegistryBrowseDialog {...defaultProps} />);

    const searchInput = screen.getByLabelText(/search module registry/i);
    await user.type(searchInput, 'nonexistentmodule');

    await waitFor(
      () => {
        expect(screen.getByText(/no modules found/i)).toBeInTheDocument();
      },
      { timeout: 3000 }
    );
  });

  it('does not render when open is false', () => {
    render(<RegistryBrowseDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Browse OpenTofu Registry')).not.toBeInTheDocument();
  });
});
