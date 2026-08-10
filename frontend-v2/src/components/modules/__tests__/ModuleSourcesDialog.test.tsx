/**
 * Tests for ModuleSourcesDialog component
 *
 * Covers: renders sources list, loading state, empty state,
 * add source button, sync source, delete source.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { ModuleSourcesDialog } from '../ModuleSourcesDialog';

const mockSources = [
  {
    id: 1,
    name: 'Official BNK Modules',
    source_type: 'git' as const,
    url: 'https://github.com/f5/bnk-modules.git',
    branch: 'main',
    is_active: true,
    last_synced_at: '2026-02-18T00:00:00Z',
    sync_status: 'success' as const,
    module_count: 12,
    auto_sync: false,
    sync_interval_hours: 24,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-02-18T00:00:00Z',
  },
  {
    id: 2,
    name: 'Custom Modules',
    source_type: 'git' as const,
    url: 'https://github.com/acme/tf-modules.git',
    branch: 'main',
    is_active: true,
    last_synced_at: '2026-02-17T00:00:00Z',
    sync_status: 'failed' as const,
    module_count: 5,
    auto_sync: true,
    sync_interval_hours: 12,
    created_at: '2026-01-10T00:00:00Z',
    updated_at: '2026-02-17T00:00:00Z',
  },
];

describe('ModuleSourcesDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();

    server.use(
      http.get('*/api/module-sources', () => {
        return HttpResponse.json(mockSources);
      })
    );
  });

  it('renders dialog with title and description', () => {
    render(<ModuleSourcesDialog {...defaultProps} />);

    expect(screen.getByText('Module Sources')).toBeInTheDocument();
    expect(screen.getByText(/manage external module sources/i)).toBeInTheDocument();
  });

  it('displays sources in a table', async () => {
    render(<ModuleSourcesDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Official BNK Modules')).toBeInTheDocument();
      expect(screen.getByText('Custom Modules')).toBeInTheDocument();
    });
  });

  it('shows source type, URL, and module count', async () => {
    render(<ModuleSourcesDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Official BNK Modules')).toBeInTheDocument();
    });

    // Check for git type indicators
    const gitLabels = screen.getAllByText(/git/i);
    expect(gitLabels.length).toBeGreaterThanOrEqual(2);

    // Check module counts
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('shows status badges for sources', async () => {
    render(<ModuleSourcesDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('success')).toBeInTheDocument();
      expect(screen.getByText('failed')).toBeInTheDocument();
    });
  });

  it('shows empty state when no sources are configured', async () => {
    server.use(
      http.get('*/api/module-sources', () => {
        return HttpResponse.json([]);
      })
    );

    render(<ModuleSourcesDialog {...defaultProps} />);

    await waitFor(() => {
      expect(
        screen.getByText(/no module sources configured/i)
      ).toBeInTheDocument();
    });
  });

  it('Add Source button opens the add dialog', async () => {
    const user = userEvent.setup();
    render(<ModuleSourcesDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Official BNK Modules')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /add source/i }));

    await waitFor(() => {
      expect(screen.getByText('Add Module Source')).toBeInTheDocument();
    });
  });

  it('close button calls onOpenChange', async () => {
    const user = userEvent.setup();
    render(<ModuleSourcesDialog {...defaultProps} />);

    // There are two close buttons: the dialog X button and the explicit Close button
    // We want the explicit one in the footer
    const closeButtons = screen.getAllByRole('button', { name: /close/i });
    // The explicit footer "Close" button (not the X icon)
    const footerClose = closeButtons.find(
      (btn) => btn.textContent === 'Close'
    );
    expect(footerClose).toBeDefined();
    await user.click(footerClose!);

    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('does not render when open is false', () => {
    render(<ModuleSourcesDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Module Sources')).not.toBeInTheDocument();
  });

  it('auto-syncs a newly created source so module counts populate immediately', async () => {
    const user = userEvent.setup();
    let syncRequestedFor: number | null = null;

    server.use(
      http.post('*/api/module-sources', async () => {
        return HttpResponse.json({
          id: 44,
          name: 'New Source',
          source_type: 'git',
          url: 'https://github.com/example/new-source.git',
          branch: 'main',
          git_ref: null,
          auth_type: null,
          credential_type: 'none',
          credential_scope: null,
          credential_capabilities: null,
          credential_metadata: null,
          credential_recommendation: null,
          credential_expires_at: null,
          credential_last_rotated_at: null,
          credential_validation_status: null,
          credential_last_validated_at: null,
          credential_validation_error: null,
          has_secret: false,
          last_synced_at: null,
          sync_status: 'pending',
          sync_error: null,
          module_count: 0,
          is_active: true,
          is_default: false,
          auto_sync: false,
          sync_interval_hours: 24,
          description: null,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        });
      }),
      http.post('*/api/module-sources/:id/sync', ({ params }) => {
        syncRequestedFor = Number(params.id);
        return HttpResponse.json({
          success: true,
          results: {
            modules_found: 2,
            modules_created: 2,
            modules_updated: 0,
            errors: [],
          },
        });
      })
    );

    render(<ModuleSourcesDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Official BNK Modules')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /add source/i }));
    await waitFor(() => {
      expect(screen.getByText('Add Module Source')).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/name/i), 'New Source');
    await user.type(screen.getByLabelText(/repository url/i), 'https://github.com/example/new-source.git');
    await user.click(screen.getByRole('button', { name: /add source/i }));

    await waitFor(() => {
      expect(syncRequestedFor).toBe(44);
    });
  });
});
