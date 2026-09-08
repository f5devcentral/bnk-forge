import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@/test/test-utils';
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

  it('shows egress association cards with captured namespaces and firewall policy', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json({
          ...emptyBnkData,
          policyAssociations: [
            {
              kind: 'egress',
              namespace: 'f5-cne-system',
              egress_name: 'bnk-egress-demo',
              snat_type: 'SRC_TRANS_AUTOMAP',
              captured_namespaces: ['bnk-egress-demo'],
              firewall_policy_name: 'egress-demo-fw',
              rules_count: 1,
              rules: [],
            },
          ],
          policyCount: 1,
        });
      })
    );

    render(<F5BNKPolicyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Egress: bnk-egress-demo')).toBeInTheDocument();
    });
    expect(screen.getByText('egress-demo-fw')).toBeInTheDocument();
    expect(screen.getByText('SNAT: SRC_TRANS_AUTOMAP')).toBeInTheDocument();
    expect(screen.getByText('ns: bnk-egress-demo')).toBeInTheDocument();
  });

  it('shows resolved address badge and clickable address-list reference for list-referenced rules', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json({
          ...emptyBnkData,
          policyAssociations: [
            {
              kind: 'egress',
              namespace: 'f5-cne-system',
              egress_name: 'bnk-egress-demo',
              snat_type: 'SRC_TRANS_AUTOMAP',
              captured_namespaces: ['bnk-egress-demo'],
              firewall_policy_name: 'egress-demo-fw',
              rules_count: 1,
              rules: [
                {
                  name: 'block-test-target',
                  action: 'drop',
                  ipProtocol: 'tcp',
                  source: { addresses: [], ports: [], addressLists: [], portLists: [] },
                  destination: {
                    addresses: ['1.1.1.1/32'],
                    ports: [],
                    addressLists: ['egress-demo-blocked'],
                    portLists: [],
                  },
                  logging: true,
                },
              ],
            },
          ],
          policyCount: 1,
        });
      })
    );

    render(<F5BNKPolicyViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Egress: bnk-egress-demo')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Show Firewall Rules'));

    expect(await screen.findByText('1.1.1.1/32')).toBeInTheDocument();
    expect(screen.getByText('from:')).toBeInTheDocument();
    expect(screen.getByText('egress-demo-blocked')).toBeInTheDocument();
  });

  it('navigates to the address list resource when its name is clicked', async () => {
    const onSelectResource = vi.fn();
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json({
          ...emptyBnkData,
          policyAssociations: [
            {
              kind: 'egress',
              namespace: 'f5-cne-system',
              egress_name: 'bnk-egress-demo',
              snat_type: 'SRC_TRANS_AUTOMAP',
              captured_namespaces: ['bnk-egress-demo'],
              firewall_policy_name: 'egress-demo-fw',
              rules_count: 1,
              rules: [
                {
                  name: 'block-test-target',
                  action: 'drop',
                  ipProtocol: 'tcp',
                  source: { addresses: [], ports: [], addressLists: [], portLists: [] },
                  destination: {
                    addresses: ['1.1.1.1/32'],
                    ports: [],
                    addressLists: ['egress-demo-blocked'],
                    portLists: [],
                  },
                  logging: true,
                },
              ],
            },
          ],
          policyCount: 1,
        });
      })
    );

    render(<F5BNKPolicyViewer clusterId={1} onSelectResource={onSelectResource} />);

    await waitFor(() => {
      expect(screen.getByText('Egress: bnk-egress-demo')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Show Firewall Rules'));

    fireEvent.click(await screen.findByText('egress-demo-blocked'));
    expect(onSelectResource).toHaveBeenCalledWith({
      kind: 'F5BigCneAddresslist',
      name: 'egress-demo-blocked',
      namespace: 'f5-cne-system',
    });
  });

  it('navigates to the firewall policy resource when its name or "Open Policy" is clicked', async () => {
    const onSelectResource = vi.fn();
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
              rules_count: 0,
              rules: [],
            },
          ],
          policyCount: 1,
        });
      })
    );

    render(<F5BNKPolicyViewer clusterId={1} onSelectResource={onSelectResource} />);

    await waitFor(() => {
      expect(screen.getByText('my-gw / http')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('fw-policy-1'));
    expect(onSelectResource).toHaveBeenLastCalledWith({
      kind: 'F5BigFwPolicy',
      name: 'fw-policy-1',
      namespace: 'bnk-demo',
    });

    fireEvent.click(screen.getByText('Open Policy'));
    expect(onSelectResource).toHaveBeenLastCalledWith({
      kind: 'F5BigFwPolicy',
      name: 'fw-policy-1',
      namespace: 'bnk-demo',
    });
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
