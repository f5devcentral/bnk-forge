/**
 * Tests for CompareRevisionsDialog component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { CompareRevisionsDialog } from '@/components/helm/CompareRevisionsDialog';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

describe('CompareRevisionsDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    clusterId: 1,
    releaseName: 'nginx-ingress',
    namespace: 'ingress-nginx',
    revision1: 1,
    revision2: 3,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    // Set up MSW handler for revision comparison (correct URL: /compare)
    server.use(
      http.get('*/api/k8s/:clusterId/helm/releases/:releaseName/compare', () => {
        return HttpResponse.json({
          success: true,
          revision1: {
            number: 1,
            values: { replicaCount: 1 },
            manifest: '---\napiVersion: v1\nkind: ConfigMap',
          },
          revision2: {
            number: 3,
            values: { replicaCount: 2 },
            manifest: '---\napiVersion: v1\nkind: ConfigMap\ndata: test',
          },
        });
      })
    );
  });

  it('renders dialog title with release name', () => {
    render(<CompareRevisionsDialog {...defaultProps} />);

    expect(screen.getByText(/Compare Revisions: nginx-ingress/)).toBeInTheDocument();
  });

  it('shows revision numbers in description', () => {
    render(<CompareRevisionsDialog {...defaultProps} />);

    expect(screen.getByText(/Comparing revision 1.*with revision 3/)).toBeInTheDocument();
  });

  it('renders Values Diff and Manifest Diff tabs after data loads', async () => {
    render(<CompareRevisionsDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /values diff/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /manifest diff/i })).toBeInTheDocument();
    });
  });

  it('renders Close button and calls onOpenChange', async () => {
    const user = userEvent.setup();
    render(<CompareRevisionsDialog {...defaultProps} />);

    // Multiple close buttons exist (X in header and "Close" at bottom)
    // Use getAllByRole and pick the last one (the explicit Close button)
    await waitFor(() => {
      const closeButtons = screen.getAllByRole('button', { name: /^close$/i });
      expect(closeButtons.length).toBeGreaterThanOrEqual(1);
    });

    const closeButtons = screen.getAllByRole('button', { name: /^close$/i });
    await user.click(closeButtons[closeButtons.length - 1]);

    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });
});
