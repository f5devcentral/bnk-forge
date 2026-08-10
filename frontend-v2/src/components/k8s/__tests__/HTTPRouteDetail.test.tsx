import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { HTTPRouteDetail } from '../HTTPRouteDetail';

const mockRoute = {
  kind: 'HTTPRoute',
  apiVersion: 'gateway.networking.k8s.io/v1',
  metadata: { name: 'app-route', namespace: 'bnk-demo', uid: '456' },
  spec: {
    parentRefs: [{ name: 'my-gateway', namespace: 'bnk-demo' }],
    hostnames: ['app.example.com'],
    rules: [
      {
        matches: [{ path: { type: 'PathPrefix', value: '/api' } }],
        backendRefs: [{ name: 'api-svc', port: 8080, weight: 100 }],
      },
    ],
  },
  status: {
    conditions: [
      { type: 'Accepted', status: 'True', reason: 'Accepted' },
    ],
  },
};

describe('HTTPRouteDetail', () => {
  it('renders summary with parent gateway', () => {
    render(<HTTPRouteDetail resource={mockRoute} />);
    expect(screen.getByText('Parent Gateway')).toBeInTheDocument();
    expect(screen.getByText('my-gateway')).toBeInTheDocument();
  });

  it('shows hostnames', () => {
    render(<HTTPRouteDetail resource={mockRoute} />);
    expect(screen.getByText('app.example.com')).toBeInTheDocument();
  });

  it('shows rules count in tab', () => {
    render(<HTTPRouteDetail resource={mockRoute} />);
    expect(screen.getByText('Rules (1)')).toBeInTheDocument();
  });
});
