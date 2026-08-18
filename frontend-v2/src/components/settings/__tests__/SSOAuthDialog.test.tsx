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

  // Hold the initiate request open for the life of the test.
  //
  // The previous versions of the two tests below mocked
  // `*/api/credential-templates/:id/sso/authenticate` -- a path the component
  // never calls (it is `/authenticate-sso`, see lib/api/credentials.ts). The
  // mock never matched, so the real request escaped msw to 127.0.0.1:3000,
  // got ECONNREFUSED, and landed in the component's catch -- often after
  // vitest had already torn the jsdom environment down, giving
  // "ReferenceError: window is not defined" from setState as an unhandled
  // rejection that failed the whole run intermittently (CI #88 on staging).
  // The 5 s setTimeout in those mocks was dead code; the actual race was the
  // wrong URL. Mock the real path, and hold it open with a promise that never
  // settles rather than a live timer that can outlive the test.
  //
  // There is deliberately no unit test for the component's isMountedRef guard:
  // in React 18 a setState on an unmounted component is a silent no-op inside
  // a live environment, so the guard has no in-process observable. The bug is
  // only visible when the environment is torn down mid-request -- which is the
  // condition these tests no longer create.
  const INITIATE = '*/api/credential-templates/:id/authenticate-sso';
  const neverSettles = () => new Promise<never>(() => {});

  it('renders dialog with title and template name', () => {
    server.use(
      http.post(INITIATE, () => neverSettles()),
    );

    render(<SSOAuthDialog {...defaultProps} />);
    expect(screen.getByText('AWS SSO Authentication')).toBeInTheDocument();
    expect(screen.getByText(/AWS SSO Dev/)).toBeInTheDocument();
  });

  it('shows initiating state initially', () => {
    server.use(
      http.post(INITIATE, () => neverSettles()),
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
