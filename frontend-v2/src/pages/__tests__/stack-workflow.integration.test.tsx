/**
 * Integration Tests — Stack (Blueprints) Workflow
 * K8S-UX-007: Featured section removed — all blueprints in one table.
 *
 * End-to-end Blueprints page lifecycle across API (MSW) and UI:
 *   1. Blueprints page renders templates from API
 *   2. Search filters blueprints
 *   3. Empty state when no blueprints
 *   4. All blueprints (including formerly "featured") render in the table
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { useAuthStore } from '@/stores/authStore';
import { mockUser, mockStackTemplates } from '@/test/test-fixtures';
import Stacks from '@/pages/Stacks';

// ---------------------------------------------------------------------------
// Navigation mock
// ---------------------------------------------------------------------------
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Override MSW handler for stack templates.
 *
 * The default handler already returns mockStackTemplates, but tests that
 * need custom data (empty list, featured templates, etc.) call this first.
 */
function setupStackHandlers(templates = mockStackTemplates) {
  server.use(
    http.get('*/api/stacks/templates', () => {
      return HttpResponse.json(templates);
    }),
    // The Stacks page also pulls imported blueprint releases and merges them
    // with templates; override that endpoint here so the count assertions in
    // these tests reflect only the template fixtures they pass in.
    http.get('*/api/blueprint-catalog/releases', () => {
      return HttpResponse.json([]);
    }),
  );
}

/** Wait for templates to appear in the table by asserting on a known template name. */
async function waitForTemplatesLoaded() {
  await waitFor(
    () => {
      expect(screen.getByText('BNK Full Stack')).toBeInTheDocument();
    },
    { timeout: 3000 },
  );
}

// ---------------------------------------------------------------------------
// Test Suite
// ---------------------------------------------------------------------------
describe('Stack Workflow (integration)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().logout();
    localStorage.clear();
    useAuthStore.getState().login('mock-jwt-token', mockUser);
  });

  // -----------------------------------------------------------------------
  // 1. Blueprints page renders templates from API
  // -----------------------------------------------------------------------
  it('renders templates from API with names and page title', async () => {
    setupStackHandlers();
    render(<Stacks />);

    await waitForTemplatesLoaded();

    // Both fixture templates visible
    expect(screen.getByText('BNK Full Stack')).toBeInTheDocument();
    expect(screen.getByText('Minimal K8s')).toBeInTheDocument();

    // Page title is "Blueprints" (D-020: rendered as the page h1).
    expect(screen.getByRole('heading', { name: 'Blueprints', level: 1 })).toBeInTheDocument();

    // D-020: subtitle is "Reusable deployment patterns. Deploy in order:
    // infrastructure → platform → solutions." — count now lives inside the
    // SectionCard title eyebrow instead.
    expect(
      screen.getByText(/infrastructure\s*→\s*platform\s*→\s*solutions/i),
    ).toBeInTheDocument();

    // SectionCard eyebrow label includes the count, e.g. "2 blueprints".
    expect(
      screen.getByText(
        new RegExp(`${mockStackTemplates.length} blueprints?`, 'i'),
      ),
    ).toBeInTheDocument();

    // Table column headers — single table now, one of each.
    expect(screen.getByRole('columnheader', { name: 'Blueprint' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'BNK Version' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Provider' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Maturity' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Category' })).toBeInTheDocument();

    // Deploy buttons — one per table row.
    const deployButtons = screen.getAllByRole('button', { name: /^deploy$/i });
    expect(deployButtons.length).toBeGreaterThanOrEqual(2);
  });

  // -----------------------------------------------------------------------
  // 2. Search filters blueprints
  // -----------------------------------------------------------------------
  it('filters blueprints when typing in the search input', async () => {
    const user = userEvent.setup();
    setupStackHandlers();
    render(<Stacks />);

    await waitForTemplatesLoaded();

    const searchInput = screen.getByLabelText('Search blueprints');
    await user.type(searchInput, 'Minimal');

    await waitFor(() => {
      expect(screen.getByText('Minimal K8s')).toBeInTheDocument();
      expect(screen.queryByText('BNK Full Stack')).not.toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // 3. Empty state when no blueprints
  // -----------------------------------------------------------------------
  it('shows empty state when API returns no templates', async () => {
    setupStackHandlers([]);

    render(<Stacks />);

    await waitFor(
      () => {
        expect(screen.getByText('No blueprints found')).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  // -----------------------------------------------------------------------
  // 4. K8S-UX-007: All blueprints in one table (no featured section)
  // -----------------------------------------------------------------------
  it('renders all blueprints in the table regardless of is_featured flag', async () => {
    const templates = [
      {
        ...mockStackTemplates[0],
        is_featured: true,
      },
      {
        ...mockStackTemplates[1],
        is_featured: false,
      },
    ];
    setupStackHandlers(templates);

    render(<Stacks />);

    await waitFor(
      () => {
        // Both templates appear in the same table
        expect(screen.getByText('BNK Full Stack')).toBeInTheDocument();
        expect(screen.getByText('Minimal K8s')).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    // No "Featured Blueprints" section exists
    expect(screen.queryByText('Featured Blueprints')).not.toBeInTheDocument();
  });
});
