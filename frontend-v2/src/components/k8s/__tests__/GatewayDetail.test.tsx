import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
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
});
