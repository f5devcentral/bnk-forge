import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { GatewayDetail } from '../GatewayDetail';

const mockGateway = {
  kind: 'Gateway',
  apiVersion: 'gateway.networking.k8s.io/v1',
  metadata: { name: 'my-gateway', namespace: 'bnk-demo', uid: '123' },
  spec: {
    gatewayClassName: 'f5-bnk',
    listeners: [
      { name: 'http', protocol: 'HTTP', port: 80 },
      { name: 'https', protocol: 'HTTPS', port: 443, tls: { mode: 'Terminate' } },
    ],
  },
  status: {
    addresses: [{ type: 'IPAddress', value: '10.1.1.100' }],
    conditions: [
      { type: 'Accepted', status: 'True', reason: 'Accepted', message: 'Gateway accepted' },
    ],
    listeners: [
      {
        name: 'http',
        attachedRoutes: 3,
        conditions: [{ type: 'Accepted', status: 'True', reason: 'Accepted', message: 'Listener accepted' }],
      },
      { name: 'https', attachedRoutes: 1, conditions: [] },
    ],
  },
};

describe('GatewayDetail', () => {
  it('renders summary tab with gateway class', () => {
    render(<GatewayDetail resource={mockGateway} />);
    expect(screen.getByText('Gateway Class')).toBeInTheDocument();
    expect(screen.getByText('f5-bnk')).toBeInTheDocument();
  });

  it('shows listener count in tabs', () => {
    render(<GatewayDetail resource={mockGateway} />);
    expect(screen.getByText('Listeners (2)')).toBeInTheDocument();
  });

  it('shows addresses from status', () => {
    render(<GatewayDetail resource={mockGateway} />);
    expect(screen.getByText('10.1.1.100')).toBeInTheDocument();
  });

  it('shows listener attached routes and conditions in listeners tab', async () => {
    render(<GatewayDetail resource={mockGateway} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Listeners (2)' }));
    expect(screen.getAllByText('Attached Routes:').length).toBe(2);
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('Listener accepted')).toBeInTheDocument();
  });
});
