import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { F5BNKPolicyViewer } from '../F5BNKPolicyViewer';
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

describe('F5BNKPolicyViewer', () => {
  beforeEach(() => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json(emptyBnkData);
      })
    );
  });

  it('shows empty state when no policies found', async () => {
    render(<F5BNKPolicyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText(/doesn't have any F5 BNK security policies/)).toBeInTheDocument();
    });
  });

  it('shows policy association cards when data returned', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json({
          ...emptyBnkData,
          policyAssociations: [
            {
              namespace: 'bnk-demo',
              gateway_name: 'my-gw',
              listener_name: 'http',
              gateway_ip: '10.1.1.100',
              port: 80,
              protocol: 'HTTP',
              bnk_policy_name: 'sec-policy-1',
              firewall_policy_name: 'fw-policy-1',
              rules_count: 2,
              rules: [],
            },
          ],
          policyCount: 1,
        });
      })
    );

    render(<F5BNKPolicyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('my-gw / http')).toBeInTheDocument();
    });
    expect(screen.getByText('sec-policy-1')).toBeInTheDocument();
    expect(screen.getByText('fw-policy-1')).toBeInTheDocument();
  });

  it('shows error state on API failure', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json({ error: 'Forbidden' }, { status: 403 });
      })
    );

    render(<F5BNKPolicyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Permission Denied')).toBeInTheDocument();
    });
  });
});
