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
  topologyCounts: {
    gateways: 0, listeners: 0, httpRoutes: 0, securityPolicies: 0,
    networkPolicies: 0, firewallPolicies: 0, iRules: 0, analyzers: 0,
    vlans: 0, cneInstances: 0, staticRoutes: 0, snatPools: 0,
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
            listeners: [{
              name: 'http',
              protocol: 'HTTP',
              port: 80,
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
});
