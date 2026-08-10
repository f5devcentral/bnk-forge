import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { ScaleDeploymentDialog } from '../ScaleDeploymentDialog';

const mockDeployment = {
  kind: 'Deployment',
  apiVersion: 'apps/v1',
  metadata: { name: 'nginx-deploy', namespace: 'default', uid: '123' },
  spec: { replicas: 3 },
  status: {},
};

describe('ScaleDeploymentDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    deployment: mockDeployment,
    onSubmit: vi.fn(),
    isLoading: false,
  };

  it('renders with current replica count', () => {
    render(<ScaleDeploymentDialog {...defaultProps} />);
    expect(screen.getByText('Scale Deployment')).toBeInTheDocument();
    expect(screen.getByText(/nginx-deploy/)).toBeInTheDocument();
    expect(screen.getByText('Current replicas: 3')).toBeInTheDocument();
  });

  it('disables Scale button when replica count unchanged', () => {
    render(<ScaleDeploymentDialog {...defaultProps} />);
    const scaleBtn = screen.getByRole('button', { name: 'Scale' });
    expect(scaleBtn).toBeDisabled();
  });

  it('calls onSubmit with new replica count', async () => {
    const user = userEvent.setup();
    render(<ScaleDeploymentDialog {...defaultProps} />);

    const input = screen.getByRole('spinbutton');
    await user.clear(input);
    await user.type(input, '5');

    const scaleBtn = screen.getByRole('button', { name: 'Scale' });
    await user.click(scaleBtn);

    expect(defaultProps.onSubmit).toHaveBeenCalledWith(5);
  });
});
