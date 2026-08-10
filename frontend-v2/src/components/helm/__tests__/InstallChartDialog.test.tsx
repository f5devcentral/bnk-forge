/**
 * Tests for InstallChartDialog component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { InstallChartDialog } from '@/components/helm/InstallChartDialog';

describe('InstallChartDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    clusterId: 1,
    defaultNamespace: 'default',
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders dialog title and description', () => {
    render(<InstallChartDialog {...defaultProps} />);

    expect(screen.getByText('Install Helm Chart')).toBeInTheDocument();
    expect(screen.getByText(/Install a Helm chart from a repository/)).toBeInTheDocument();
  });

  it('renders required form fields with labels', () => {
    render(<InstallChartDialog {...defaultProps} />);

    // Use htmlFor-based label resolution (id="release-name", id="chart")
    expect(screen.getByLabelText(/release name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/chart reference/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^namespace$/i)).toBeInTheDocument();
  });

  it('Install button is disabled when required fields are empty', () => {
    render(<InstallChartDialog {...defaultProps} />);

    const installButton = screen.getByRole('button', { name: /^install$/i });
    expect(installButton).toBeDisabled();
  });

  it('Install button is enabled when required fields are filled', async () => {
    const user = userEvent.setup();
    render(<InstallChartDialog {...defaultProps} />);

    await user.type(screen.getByLabelText(/release name/i), 'my-release');
    await user.type(screen.getByLabelText(/chart reference/i), 'bitnami/nginx');

    const installButton = screen.getByRole('button', { name: /^install$/i });
    expect(installButton).not.toBeDisabled();
  });

  it('pre-fills chart name when selectedChart is provided', () => {
    render(
      <InstallChartDialog
        {...defaultProps}
        selectedChart={{ name: 'bitnami/nginx', version: '15.4.0' }}
      />
    );

    const chartInput = screen.getByLabelText(/chart reference/i);
    expect(chartInput).toHaveValue('bitnami/nginx');
  });

  it('renders YAML and JSON tabs for custom values', () => {
    render(<InstallChartDialog {...defaultProps} />);

    expect(screen.getByRole('tab', { name: /yaml/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /json/i })).toBeInTheDocument();
  });

  it('renders options checkboxes', () => {
    render(<InstallChartDialog {...defaultProps} />);

    expect(screen.getByText(/create namespace if it doesn't exist/i)).toBeInTheDocument();
    expect(screen.getByText(/wait for resources to be ready/i)).toBeInTheDocument();
  });

  it('does not render when open is false', () => {
    render(<InstallChartDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Install Helm Chart')).not.toBeInTheDocument();
  });
});
