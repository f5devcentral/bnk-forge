import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { F5BNKTopologyViewer } from '../F5BNKTopologyViewer';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

const emptyBnkData = {
  health: null,
  topology: [],
  dataPlane: {
    vlans: [],
    cneInstances: [],
    staticRoutes: [],
    snatPools: [],
    egresses: [],
    logging: { hslPublishers: [], logProfiles: [] },
  },
  referenceGrants: [],
  topologyCounts: {
    gateways: 0, listeners: 0, httpRoutes: 0, grpcRoutes: 0, tcpRoutes: 0,
    udpRoutes: 0, tlsRoutes: 0, l4Routes: 0, totalRoutes: 0, referenceGrants: 0,
    securityPolicies: 0, networkPolicies: 0, firewallPolicies: 0, iRules: 0,
    analyzers: 0, vlans: 0, cneInstances: 0, staticRoutes: 0, snatPools: 0,
    egresses: 0, hslPublishers: 0, logProfiles: 0,
  },
  policyAssociations: [],
  policyCount: 0,
};

describe('F5BNKTopologyViewer', () => {
  beforeEach(() => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json(emptyBnkData);
      })
    );
  });

  it('shows empty state when no topology data', async () => {
    render(<F5BNKTopologyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('No BNK Resources Found')).toBeInTheDocument();
    });
  });

  it('shows gateway topology tree when data present', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json({
          ...emptyBnkData,
          topology: [{
            name: 'bnk-gateway',
            namespace: 'bnk-demo',
            gatewayClassName: 'f5-bnk',
            addresses: ['10.1.1.100'],
            accepted: true,
            programmed: true,
            conditions: [{ type: 'Accepted', status: 'True' }],
            listeners: [{
              name: 'http',
              protocol: 'HTTP',
              port: 80,
              attachedRouteCount: 0,
              conditions: [{ type: 'Accepted', status: 'True' }],
              routes: [],
              networkPolicies: [],
            }],
            securityPolicies: [],
          }],
          topologyCounts: {
            ...emptyBnkData.topologyCounts,
            gateways: 1,
            listeners: 1,
          },
        });
      })
    );

    render(<F5BNKTopologyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('bnk-gateway')).toBeInTheDocument();
    });
    expect(screen.getByText('f5-bnk')).toBeInTheDocument();
  });

  it('shows error state on failure', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json({ error: 'Server error' }, { status: 500 });
      })
    );

    render(<F5BNKTopologyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load topology')).toBeInTheDocument();
    });
  });

  it('shows operational status badges on gateway and listener nodes', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json({
          ...emptyBnkData,
          topology: [{
            name: 'bnk-gateway',
            namespace: 'bnk-demo',
            gatewayClassName: 'f5-bnk',
            addresses: ['10.1.1.100'],
            accepted: true,
            programmed: false,
            conditions: [
              { type: 'Accepted', status: 'True' },
              { type: 'Programmed', status: 'False', message: 'address conflict' },
            ],
            listeners: [{
              name: 'http',
              protocol: 'HTTP',
              port: 80,
              attachedRouteCount: 2,
              conditions: [{ type: 'Accepted', status: 'True' }],
              routes: [],
              networkPolicies: [],
            }],
            securityPolicies: [],
          }],
          topologyCounts: {
            ...emptyBnkData.topologyCounts,
            gateways: 1,
            listeners: 1,
          },
        });
      })
    );

    render(<F5BNKTopologyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('bnk-gateway')).toBeInTheDocument();
    });
    expect(screen.getByText('2 routes')).toBeInTheDocument();
    expect(screen.getByText('Healthy')).toBeInTheDocument();
  });

  it('shows reference grants section when grants exist', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json({
          ...emptyBnkData,
          topology: [{
            name: 'bnk-gateway',
            namespace: 'bnk-demo',
            gatewayClassName: 'f5-bnk',
            addresses: ['10.1.1.100'],
            accepted: true,
            programmed: true,
            conditions: [{ type: 'Accepted', status: 'True' }],
            listeners: [{
              name: 'http',
              protocol: 'HTTP',
              port: 80,
              attachedRouteCount: 0,
              conditions: [],
              routes: [],
              networkPolicies: [],
            }],
            securityPolicies: [],
          }],
          referenceGrants: [{
            name: 'rg-1',
            namespace: 'bnk-demo',
            from: [{ group: 'gateway.networking.k8s.io', kind: 'HTTPRoute', namespace: 'app' }],
            to: [{ group: '', kind: 'Service' }],
          }],
          topologyCounts: {
            ...emptyBnkData.topologyCounts,
            gateways: 1,
            listeners: 1,
            referenceGrants: 1,
          },
        });
      })
    );

    render(<F5BNKTopologyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('bnk-gateway')).toBeInTheDocument();
    });
    expect(screen.getByText('Reference Grants')).toBeInTheDocument();
    expect(screen.getByText('rg-1')).toBeInTheDocument();
  });

  it('shows traffic stat badges on listeners and egresses', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json({
          ...emptyBnkData,
          topology: [{
            name: 'bnk-gateway',
            namespace: 'bnk-demo',
            gatewayClassName: 'f5-bnk',
            addresses: ['10.1.1.100'],
            accepted: true,
            programmed: true,
            conditions: [{ type: 'Accepted', status: 'True' }],
            listeners: [{
              name: 'http',
              protocol: 'HTTP',
              port: 80,
              attachedRouteCount: 0,
              conditions: [{ type: 'Accepted', status: 'True' }],
              routes: [],
              networkPolicies: [],
            }],
            securityPolicies: [],
          }],
          topologyCounts: {
            ...emptyBnkData.topologyCounts,
            gateways: 1,
            listeners: 1,
            egresses: 1,
          },
          dataPlane: {
            ...emptyBnkData.dataPlane,
            egresses: [{
              name: 'default-egress',
              namespace: 'bnk-demo',
              snatType: 'SRC_TRANS_AUTOMAP',
              egressSnatpool: null,
              firewallEnforcedPolicy: null,
              logProfile: null,
              capturedNamespaces: [],
              vxlan: null,
              ready: true,
            }],
          },
          trafficStats: {
            source: 'tmctl',
            podName: 'f5-tmm-abc',
            sampledAt: '2026-09-01T00:00:00Z',
            available: true,
            error: null,
            listeners: [{
              gatewayName: 'bnk-gateway',
              gatewayNamespace: 'bnk-demo',
              listenerName: 'http',
              clientsideBytesIn: 1024,
              clientsideBytesOut: 2048,
              clientsideCurConns: 5,
              clientsideTotConns: 100,
              serversideBytesIn: 0,
              serversideBytesOut: 0,
              serversideCurConns: 0,
              serversideTotConns: 0,
            }],
            egresses: [{
              egressName: 'default-egress',
              namespace: 'bnk-demo',
              clientsideBytesIn: 512,
              clientsideBytesOut: 256,
              clientsideCurConns: 2,
              clientsideTotConns: 42,
              serversideBytesIn: 0,
              serversideBytesOut: 0,
              serversideCurConns: 0,
              serversideTotConns: 0,
            }],
            firewallRules: [],
          },
        });
      })
    );

    render(<F5BNKTopologyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('bnk-gateway')).toBeInTheDocument();
    });
    expect(screen.getByText('5 conns')).toBeInTheDocument();
    expect(screen.getByText('100 total')).toBeInTheDocument();
  });
});
