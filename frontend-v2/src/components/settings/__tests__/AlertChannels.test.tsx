/**
 * Tests for AlertChannels component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { AlertChannels } from '../AlertChannels';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AlertChannels', () => {
  it('renders channel list after loading', async () => {
    server.use(
      http.get('*/api/alert-channels', () =>
        HttpResponse.json([
          {
            id: 1,
            name: 'Slack #ops',
            channel_type: 'slack',
            enabled: true,
            url: 'https://hooks.slack.com/services/xxx',
            event_types: ['health_change'],
            min_interval_seconds: 60,
            total_sent: 10,
            total_failed: 0,
            last_fired_at: null,
            last_error: null,
          },
        ])
      ),
    );

    render(<AlertChannels />);

    await waitFor(() => {
      expect(screen.getByText('Slack #ops')).toBeInTheDocument();
    });
    expect(screen.getByText('Slack')).toBeInTheDocument();
  });

  it('shows empty state when no channels exist', async () => {
    server.use(
      http.get('*/api/alert-channels', () => HttpResponse.json([])),
    );

    render(<AlertChannels />);

    await waitFor(() => {
      expect(screen.getByText('No alert channels configured')).toBeInTheDocument();
    });
    expect(screen.getByText('Create your first channel')).toBeInTheDocument();
  });

  it('opens create dialog when Add Channel is clicked', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('*/api/alert-channels', () => HttpResponse.json([])),
    );

    render(<AlertChannels />);

    await waitFor(() => {
      expect(screen.getByText('Add Channel')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Add Channel'));

    await waitFor(() => {
      expect(screen.getByText('Create Alert Channel')).toBeInTheDocument();
    });
    expect(screen.getByLabelText('Name *')).toBeInTheDocument();
  });
});
