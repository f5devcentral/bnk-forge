import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { DriftSettings } from '../DriftSettings';

beforeEach(() => {
  vi.clearAllMocks();
  server.use(
    http.get('*/api/projects/:projectId/drift/settings', () => {
      return HttpResponse.json({
        enabled: true,
        schedule_type: 'interval',
        schedule_value: '3600',
        check_all_modules: true,
        notify_on_drift: true,
        ignore_insignificant_changes: true,
      });
    })
  );
});

describe('DriftSettings', () => {
  it('renders drift detection settings with enabled badge', async () => {
    render(<DriftSettings projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Drift Detection Settings')).toBeInTheDocument();
    });
    expect(screen.getByText('Enabled')).toBeInTheDocument();
    expect(screen.getByText('Disable')).toBeInTheDocument();
  });

  it('renders disabled state with enable button', async () => {
    server.use(
      http.get('*/api/projects/:projectId/drift/settings', () => {
        return HttpResponse.json({
          enabled: false,
          schedule_type: 'interval',
          schedule_value: '3600',
          check_all_modules: true,
          notify_on_drift: true,
          ignore_insignificant_changes: true,
        });
      })
    );

    render(<DriftSettings projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Disabled')).toBeInTheDocument();
    });
    expect(screen.getByText('Enable')).toBeInTheDocument();
    // When disabled, the save button should be disabled
    expect(screen.getByText('Save Settings').closest('button')).toBeDisabled();
  });

  it('shows error alert when settings fail to load', async () => {
    server.use(
      http.get('*/api/projects/:projectId/drift/settings', () => {
        return HttpResponse.json(
          { error: 'Failed to load' },
          { status: 500 }
        );
      })
    );

    render(<DriftSettings projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText(/failed to load drift settings/i)).toBeInTheDocument();
    });
  });
});
