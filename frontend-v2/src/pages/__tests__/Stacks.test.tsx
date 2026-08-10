/**
 * Tests for Stacks (Blueprints) page.
 *
 * Covers: renders blueprint list, loading state, search filter, chip
 * categories, imported blueprint releases (catalog badge), empty state.
 *
 * D-020: page was redesigned to a single bnkhealth-shaped surface — chip
 * filter row at the top + one flat table. Old "sidebar with categories +
 * step badges in card headers" assertions removed.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import Stacks from '@/pages/Stacks';

const mockNavigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe('Stacks', () => {
  it('shows deployment flow guidance in the page subtitle', async () => {
    render(<Stacks />);
    // Bnkhealth-shaped subtitle: "Reusable deployment patterns. Deploy in
    // order: infrastructure → platform → solutions."
    await waitFor(() => {
      expect(
        screen.getByText(/infrastructure\s*→\s*platform\s*→\s*solutions/i),
      ).toBeInTheDocument();
    });
  });

  it('renders page title "Blueprints"', async () => {
    render(<Stacks />);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Blueprints', level: 1 })).toBeInTheDocument();
    });
  });

  it('renders loading skeletons while data is fetching', () => {
    server.use(
      http.get('*/api/stacks/templates', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json([]);
      }),
    );
    render(<Stacks />);
    const skeletons = document.querySelectorAll('[class*="animate-pulse"], [class*="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('renders blueprint names from API data', async () => {
    render(<Stacks />);
    await waitFor(() => {
      const blueprintElements = screen.queryAllByText(/blueprint|stack|aws|demo/i);
      expect(blueprintElements.length).toBeGreaterThan(0);
    });
  });

  it('renders imported blueprint releases from blueprint catalog API', async () => {
    render(<Stacks />);
    await waitFor(() => {
      expect(
        screen.getByText('BIG-IP Next for Kubernetes on IBM ROKS Single NIC'),
      ).toBeInTheDocument();
    });
    // Catalog badge text shortened from "Catalog Release" to "Catalog" in the redesign.
    expect(screen.getByText('Catalog')).toBeInTheDocument();
  });

  it('renders category chip row with at least one category', async () => {
    render(<Stacks />);
    // Wait for the fixture data to render — only categories with count > 0
    // appear as chips. mockStackTemplates covers the bnk category, so
    // "Platform (BNK)" is the most reliable assertion.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Platform \(BNK\)/ })).toBeInTheDocument();
    });
    // "All" chip is always rendered.
    expect(screen.getByRole('button', { name: /^All\b/ })).toBeInTheDocument();
  });

  it('shows blueprint count in the SectionCard title', async () => {
    render(<Stacks />);
    // SectionCard title renders as an eyebrow label like "2 blueprints".
    await waitFor(() => {
      expect(screen.getByText(/\d+ blueprints?/i)).toBeInTheDocument();
    });
  });

  it('filters blueprints by search query', async () => {
    const user = userEvent.setup();
    render(<Stacks />);
    await waitFor(() => {
      const searchInput = screen.getByPlaceholderText(/search/i);
      expect(searchInput).toBeInTheDocument();
    });
    const searchInput = screen.getByPlaceholderText(/search/i);
    await user.type(searchInput, 'nonexistent-blueprint-xyz');
    await waitFor(() => {
      const tableRows = document.querySelectorAll('tr');
      // Header row may still exist; data rows should be filtered out.
      expect(tableRows.length).toBeLessThanOrEqual(2);
    });
  });

  it('renders empty state when no blueprints exist', async () => {
    server.use(
      http.get('*/api/stacks/templates', () => HttpResponse.json([])),
      http.get('*/api/blueprint-catalog/releases', () => HttpResponse.json([])),
    );
    render(<Stacks />);
    await waitFor(() => {
      expect(screen.getByText('No blueprints found')).toBeInTheDocument();
    });
  });

  it('opens builtin-only CLI releases with imported blueprint dialog', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('*/api/stacks/templates', () => HttpResponse.json([])),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 43,
            blueprint_source_id: 1,
            source_name: 'builtin-blueprints',
            source_type: 'builtin',
            blueprint_id: 'awsbnkctl-bnk-demo',
            blueprint_version: '1.0.0',
            blueprint_name: 'AWS BNK Demo (CLI Deploy)',
            blueprint_description: 'CLI deploy blueprint release',
            category: 'infrastructure',
            cloud_provider: 'aws',
            tags: ['awsbnkctl', 'cli'],
            schema_version: 1,
            source_path: 'data/blueprints/awsbnkctl-bnk-demo/forge-blueprint.json',
            source_ref: null,
            content_sha256: 'c'.repeat(64),
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'imported',
            state_reason: null,
            is_active: true,
            is_featured: false,
            imported_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
        ]),
      ),
      http.get('*/api/stacks/releases/43', () =>
        HttpResponse.json({
          id: 1000043,
          name: 'AWS BNK Demo (CLI Deploy)',
          slug: 'release-43',
          description: 'CLI deploy blueprint release',
          category: 'infrastructure',
          cloud_provider: 'aws',
          icon: 'Layers',
          color: 'blue',
          estimated_time: '5-10 minutes',
          estimated_cost: 'Zero (dry-run only)',
          difficulty: 'beginner',
          modules: [
            {
              path: 'cli-bnkctl/awsbnkctl/bnk-demo',
              name: 'AWS BNK Demo',
              required: true,
              description: 'Single-module CLI deploy of bnk-demo topology via awsbnkctl',
              variables: {},
              module_catalog_status: 'available',
            },
          ],
          variable_templates: {},
          prerequisites: [],
          tags: ['awsbnkctl', 'cli'],
          maturity: 'reference',
          outcomes: ['Validation via awsbnkctl up --dry-run'],
          platform_defaults: {},
          is_active: true,
          is_featured: false,
          version: '1.0.0',
          created_by: 'blueprint-catalog',
          is_public: true,
          forked_from: null,
          source_kind: 'blueprint_release',
          blueprint_release_id: 43,
          blueprint_source_id: 1,
          release_state: 'imported',
          validation_state: 'valid',
          source_path: 'data/blueprints/awsbnkctl-bnk-demo/forge-blueprint.json',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
      ),
      http.get('*/api/stacks/releases/43/required-inputs', () =>
        HttpResponse.json({
          template_slug: 'release-43',
          template_name: 'AWS BNK Demo (CLI Deploy)',
          inputs_by_module: {},
          all_inputs: [
            {
              name: 'cluster_name',
              type: 'string',
              description: 'Cluster name (metadata.name)',
              default: 'bnk-demo',
              module_path: 'blueprint',
              module_name: 'AWS BNK Demo (CLI Deploy)',
              required: true,
            },
          ],
          total_required: 1,
          total_optional: 0,
          missing_modules: [],
          summary: [],
        }),
      ),
    );

    render(<Stacks />);

    await waitFor(() => {
      expect(screen.getByText('AWS BNK Demo (CLI Deploy)')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Deploy' }));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toHaveTextContent('Imported Blueprint');
      expect(screen.getByRole('dialog')).toHaveTextContent('cli-bnkctl/awsbnkctl/bnk-demo');
    });
  });
});
