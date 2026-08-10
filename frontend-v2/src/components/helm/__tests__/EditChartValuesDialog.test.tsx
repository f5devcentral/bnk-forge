/**
 * Tests for EditChartValuesDialog component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { EditChartValuesDialog } from '@/components/helm/EditChartValuesDialog';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

describe('EditChartValuesDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    chartId: 1,
    chartName: 'nginx',
    chartVersion: '15.4.0',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    // Set up chart values endpoint
    server.use(
      http.get('*/api/helm/charts/:chartId/values', () => {
        return HttpResponse.json({
          values: 'replicaCount: 2\nimage:\n  tag: latest',
        });
      })
    );
  });

  it('renders dialog title and description', () => {
    render(<EditChartValuesDialog {...defaultProps} />);

    expect(screen.getByText('Edit Chart Values')).toBeInTheDocument();
    expect(screen.getByText(/nginx:15.4.0/)).toBeInTheDocument();
  });

  it('renders Save Changes and Cancel buttons', () => {
    render(<EditChartValuesDialog {...defaultProps} />);

    expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  it('Save Changes button is disabled initially (no changes)', async () => {
    render(<EditChartValuesDialog {...defaultProps} />);

    await waitFor(() => {
      const saveButton = screen.getByRole('button', { name: /save changes/i });
      expect(saveButton).toBeDisabled();
    });
  });

  it('does not render when open is false', () => {
    render(<EditChartValuesDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Edit Chart Values')).not.toBeInTheDocument();
  });
});
