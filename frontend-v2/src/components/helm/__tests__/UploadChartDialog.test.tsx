/**
 * Tests for UploadChartDialog component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { UploadChartDialog } from '@/components/helm/UploadChartDialog';

describe('UploadChartDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders dialog title and description', () => {
    render(<UploadChartDialog {...defaultProps} />);

    expect(screen.getByText('Upload Helm Chart')).toBeInTheDocument();
    expect(screen.getByText(/Upload a custom Helm chart package/)).toBeInTheDocument();
  });

  it('renders drag and drop area with instructions', () => {
    render(<UploadChartDialog {...defaultProps} />);

    expect(screen.getByText(/drag and drop your chart here/i)).toBeInTheDocument();
    expect(screen.getByText(/or click to browse/i)).toBeInTheDocument();
  });

  it('renders chart requirements info box', () => {
    render(<UploadChartDialog {...defaultProps} />);

    expect(screen.getByText(/chart requirements/i)).toBeInTheDocument();
    expect(screen.getByText(/must be a valid Helm chart package/i)).toBeInTheDocument();
  });

  it('Upload Chart button is disabled when no file is selected', () => {
    render(<UploadChartDialog {...defaultProps} />);

    const uploadButton = screen.getByRole('button', { name: /upload chart/i });
    expect(uploadButton).toBeDisabled();
  });

  it('renders Cancel button', () => {
    render(<UploadChartDialog {...defaultProps} />);

    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  it('does not render when open is false', () => {
    render(<UploadChartDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Upload Helm Chart')).not.toBeInTheDocument();
  });
});
