/**
 * Tests for ModuleLibraryVersion component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import ModuleLibraryVersion from '../ModuleLibraryVersion';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ModuleLibraryVersion', () => {
  it('renders version info after loading', async () => {
    server.use(
      http.get('*/api/module-library/version', () =>
        HttpResponse.json({
          synced_version: '1.2.3',
          latest_version: '1.2.3',
          update_available: false,
          last_synced_at: '2026-02-01T00:00:00',
          git_ref: 'main',
          error: null,
        })
      ),
    );

    render(<ModuleLibraryVersion />);

    expect(screen.getByText('Module library')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('v1.2.3')).toBeInTheDocument();
    });
    expect(screen.getByText('Up to date')).toBeInTheDocument();
  });

  it('shows update available badge when new version exists', async () => {
    server.use(
      http.get('*/api/module-library/version', () =>
        HttpResponse.json({
          synced_version: '1.2.0',
          latest_version: '1.3.0',
          update_available: true,
          last_synced_at: '2026-01-15T00:00:00',
          git_ref: 'main',
          error: null,
        })
      ),
    );

    render(<ModuleLibraryVersion />);

    await waitFor(() => {
      expect(screen.getByText('Update Available')).toBeInTheDocument();
    });
  });
});
