/**
 * Tests for SSOAuthDialog component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { SSOAuthDialog } from '../SSOAuthDialog';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SSOAuthDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    templateId: 1,
    templateName: 'AWS SSO Dev',
    onSuccess: vi.fn(),
  };

  it('renders dialog with title and template name', () => {
    server.use(
      http.post('*/api/credential-templates/:id/sso/authenticate', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({});
      }),
    );

    render(<SSOAuthDialog {...defaultProps} />);
    expect(screen.getByText('AWS SSO Authentication')).toBeInTheDocument();
    expect(screen.getByText(/AWS SSO Dev/)).toBeInTheDocument();
  });

  it('shows initiating state initially', () => {
    server.use(
      http.post('*/api/credential-templates/:id/sso/authenticate', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({});
      }),
    );

    render(<SSOAuthDialog {...defaultProps} />);
    expect(screen.getByText('Initiating SSO flow...')).toBeInTheDocument();
  });

  it('shows error state when SSO initiation fails', async () => {
    server.use(
      http.post('*/api/credential-templates/:id/authenticate-sso', () =>
        HttpResponse.json(
          { detail: 'SSO not configured for this template' },
          { status: 400 }
        )
      ),
    );

    render(<SSOAuthDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Try Again')).toBeInTheDocument();
    });
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });
});
