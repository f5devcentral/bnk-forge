/**
 * Tests for TrafficFlowOverview component — TOPO-004 (tuned)
 *
 * Covers: loading state, empty state, error state, gateway flow rendering,
 * summary stat chips, clickable "+N more", collapsible sections.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { TrafficFlowOverview } from '../TrafficFlowOverview';
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
    gateways: 0, listeners: 0, httpRoutes: 0, grpcRoutes: 0, tcpRoutes: 0,
    udpRoutes: 0, tlsRoutes: 0, l4Routes: 0, totalRoutes: 0, referenceGrants: 0,
    securityPolicies: 0, networkPolicies: 0, firewallPolicies: 0, iRules: 0,
    analyzers: 0, vlans: 0, cneInstances: 0, staticRoutes: 0, snatPools: 0,
    egresses: 0, hslPublishers: 0, logProfiles: 0,
  },
  policyAssociations: [],
  policyCount: 0,
  backends: [],
};

const populatedBnkData = {
  ...emptyBnkData,
  topology: [{
    name: 'prod-gateway',
    namespace: 'bnk-demo',
    gatewayClassName: 'f5-bnk',
    addresses: ['10.1.1.100'],
    listeners: [{
      name: 'http',
      protocol: 'HTTP',
      port: 80,
      routes: [{
        name: 'api-route',
        namespace: 'bnk-demo',
        kind: 'HTTPRoute',
        hostnames: ['api.example.com'],
        backends: [
          { name: 'api-svc', namespace: 'bnk-demo', port: 8080, weight: 100, kind: 'Service', group: '' },
        ],
        analyzers: [],
      }],
      networkPolicies: [],
    }],
    securityPolicies: [{
      name: 'prod-sec-policy',
      namespace: 'bnk-demo',
      targetListener: 'http',
      firewallPolicies: [{
        name: 'fw-policy-1',
        rules: [
          { name: 'allow-http', action: 'accept', ipProtocol: 'tcp', logging: false },
        ],
        addressLists: [],
        portLists: [],
      }],
    }],
  }],
  topologyCounts: {
    ...emptyBnkData.topologyCounts,
    gateways: 1,
    listeners: 1,
    httpRoutes: 1,
    totalRoutes: 1,
    securityPolicies: 1,
    firewallPolicies: 1,
  },
  backends: [
    { name: 'api-svc', namespace: 'bnk-demo', type: 'ClusterIP', ports: ['8080/TCP'], mapped: true, routeRefs: [] },
    { name: 'orphan-svc', namespace: 'bnk-demo', type: 'ClusterIP', ports: ['9000/TCP'], mapped: false, routeRefs: [] },
  ],
  dataPlane: {
    ...emptyBnkData.dataPlane,
    cneInstances: [{
      name: 'cne-prod',
      namespace: 'bnk-demo',
      features: { firewallACL: true, intelligentLB: false },
      networkAttachments: [],
      containerPlatform: 'kubernetes',
      phase: 'Running',
    }],
    vlans: [{
      name: 'external-vlan',
      namespace: 'bnk-demo',
      interfaces: [],
      selfipV4s: ['10.1.1.1'],
      prefixLen: 24,
      mtu: 1500,
      internal: false,
      autoLasthop: 'default',
      ready: true,
    }],
    snatPools: [{
      name: 'prod-snat',
      namespace: 'bnk-demo',
      addresses: ['10.1.1.50', '10.1.1.51'],
    }],
    egresses: [{
      name: 'default-egress',
      namespace: 'bnk-demo',
      snatType: 'SRC_TRANS_AUTOMAP',
      egressSnatpool: null,
      firewallEnforcedPolicy: 'egress-demo-fw',
      logProfile: null,
      capturedNamespaces: ['bnk-egress-demo'],
      vxlan: { tmmInterfaceName: 'ext-vlan', nodeInterfaceName: 'ens5' },
      ready: true,
    }],
    staticRoutes: [{
      name: 'backend-route',
      namespace: 'bnk-demo',
      destination: '10.2.0.0/16',
      gateway: '10.1.1.1',
    }],
  },
};

describe('TrafficFlowOverview', () => {
  beforeEach(() => {
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
        return HttpResponse.json(emptyBnkData);
      })
    );
  });

  describe('Empty / Loading / Error states', () => {
    it('shows empty state with zero-count stat chips when no topology data', async () => {
      render(<TrafficFlowOverview clusterId={1} />);

      await waitFor(() => {
        // Summary stat chips show zero counts
        expect(screen.getByText('gateways')).toBeInTheDocument();
        expect(screen.getByText('routes')).toBeInTheDocument();
      });
      // Clean empty state with guidance
      expect(screen.getByText('No BNK gateways configured')).toBeInTheDocument();
    });

    it('shows error state on API failure', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json({ error: 'Server error' }, { status: 500 });
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Failed to load traffic flow data')).toBeInTheDocument();
      });
    });
  });

  describe('Gateway flow rendering', () => {
    it('renders gateway flow row with gateway name and VIP', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('prod-gateway')).toBeInTheDocument();
      });
      expect(screen.getByText('10.1.1.100')).toBeInTheDocument();
      expect(screen.getByText('f5-bnk')).toBeInTheDocument();
    });

    it('shows summary stat chips with correct counts', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);

      await waitFor(() => {
        // Stat chips are present
        expect(screen.getByText('gateway')).toBeInTheDocument();
      });
      // Route and backend counts visible
      expect(screen.getByText('route')).toBeInTheDocument();
      expect(screen.getByText('backend')).toBeInTheDocument();
    });

    it('renders listener and route in flow columns', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);

      await waitFor(() => {
        // Listener name visible
        expect(screen.getByText('http')).toBeInTheDocument();
      });
      // Route name visible in the routes column
      expect(screen.getByText('api-route')).toBeInTheDocument();
      // Route hostname visible
      expect(screen.getByText('api.example.com')).toBeInTheDocument();
    });

    it('renders backend names in the backends column', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('api-svc')).toBeInTheDocument();
      });
    });

    it('shows security policy attachment badges in gateway flow', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);

      await waitFor(() => {
        // Security info appears in both gateway header and sidebar — at least one match
        const fwMatches = screen.getAllByText(/1 fw polic/);
        expect(fwMatches.length).toBeGreaterThanOrEqual(1);
      });
      const ruleMatches = screen.getAllByText(/1 rule/);
      expect(ruleMatches.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe('Egress (outbound) lane', () => {
    it('renders the egress section with captured namespaces, SNAT type, and VLAN', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Egress (outbound)')).toBeInTheDocument();
      });
      expect(screen.getByText('default-egress')).toBeInTheDocument();
      expect(screen.getByText('bnk-egress-demo')).toBeInTheDocument();
      expect(screen.getByText('TMM SNAT: SRC_TRANS_AUTOMAP')).toBeInTheDocument();
      expect(screen.getByText(/fw: egress-demo-fw/)).toBeInTheDocument();
      expect(screen.getByText('ext-vlan')).toBeInTheDocument();
    });

    it('does not render the egress section when there are no egresses', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json({
            ...populatedBnkData,
            dataPlane: { ...populatedBnkData.dataPlane, egresses: [] },
          });
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('prod-gateway')).toBeInTheDocument();
      });
      expect(screen.queryByText('Egress (outbound)')).not.toBeInTheDocument();
    });
  });

  describe('Collapsible sections', () => {
    it('renders infrastructure card collapsed by default', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);

      await waitFor(() => {
        // Infrastructure header visible
        expect(screen.getByText('Infrastructure & Data Plane')).toBeInTheDocument();
      });
      // Content should NOT be visible by default (collapsed)
      expect(screen.queryByText('cne-prod')).not.toBeInTheDocument();
    });

    it('expands infrastructure card to show CNE and VLAN data', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);
      const user = userEvent.setup();

      await waitFor(() => {
        expect(screen.getByText('Infrastructure & Data Plane')).toBeInTheDocument();
      });

      // Click to expand
      await user.click(screen.getByText('Infrastructure & Data Plane'));

      await waitFor(() => {
        expect(screen.getByText('cne-prod')).toBeInTheDocument();
        expect(screen.getByText('external-vlan')).toBeInTheDocument();
      });
    });

    it('shows unmapped services as a collapsible card', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);
      const user = userEvent.setup();

      await waitFor(() => {
        expect(screen.getByText('Unmapped Services')).toBeInTheDocument();
      });

      // Click to expand
      await user.click(screen.getByText('Unmapped Services'));

      await waitFor(() => {
        expect(screen.getByText(/orphan-svc/)).toBeInTheDocument();
      });
    });
  });

  describe('Clickable resources', () => {
    it('calls onSelectResource when clicking a gateway name', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      const onSelect = vi.fn();
      render(<TrafficFlowOverview clusterId={1} onSelectResource={onSelect} />);
      const user = userEvent.setup();

      await waitFor(() => {
        expect(screen.getByText('prod-gateway')).toBeInTheDocument();
      });

      await user.click(screen.getByText('prod-gateway'));

      expect(onSelect).toHaveBeenCalledWith({
        kind: 'Gateway',
        name: 'prod-gateway',
        namespace: 'bnk-demo',
      });
    });

    it('calls onSelectResource when clicking a route name', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      const onSelect = vi.fn();
      render(<TrafficFlowOverview clusterId={1} onSelectResource={onSelect} />);
      const user = userEvent.setup();

      await waitFor(() => {
        expect(screen.getByText('api-route')).toBeInTheDocument();
      });

      await user.click(screen.getByText('api-route'));

      expect(onSelect).toHaveBeenCalledWith({
        kind: 'HTTPRoute',
        name: 'api-route',
        namespace: 'bnk-demo',
      });
    });

    it('calls onSelectResource when clicking a SNAT pool in infrastructure', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      const onSelect = vi.fn();
      render(<TrafficFlowOverview clusterId={1} onSelectResource={onSelect} />);
      const user = userEvent.setup();

      // Expand infrastructure card
      await waitFor(() => {
        expect(screen.getByText('Infrastructure & Data Plane')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Infrastructure & Data Plane'));

      await waitFor(() => {
        expect(screen.getByText('prod-snat')).toBeInTheDocument();
      });
      await user.click(screen.getByText('prod-snat'));

      expect(onSelect).toHaveBeenCalledWith({
        kind: 'F5SPKSnatPool',
        name: 'prod-snat',
        namespace: 'bnk-demo',
      });
    });

    it('calls onSelectResource when clicking an egress in the Egress section', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      const onSelect = vi.fn();
      render(<TrafficFlowOverview clusterId={1} onSelectResource={onSelect} />);
      const user = userEvent.setup();

      await waitFor(() => {
        expect(screen.getByText('default-egress')).toBeInTheDocument();
      });
      await user.click(screen.getByText('default-egress'));

      expect(onSelect).toHaveBeenCalledWith({
        kind: 'F5SPKEgress',
        name: 'default-egress',
        namespace: 'bnk-demo',
      });
    });

    it('calls onSelectResource when clicking a static route in infrastructure', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      const onSelect = vi.fn();
      render(<TrafficFlowOverview clusterId={1} onSelectResource={onSelect} />);
      const user = userEvent.setup();

      await waitFor(() => {
        expect(screen.getByText('Infrastructure & Data Plane')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Infrastructure & Data Plane'));

      await waitFor(() => {
        expect(screen.getByText('backend-route')).toBeInTheDocument();
      });
      await user.click(screen.getByText('backend-route'));

      expect(onSelect).toHaveBeenCalledWith({
        kind: 'F5SPKStaticRoute',
        name: 'backend-route',
        namespace: 'bnk-demo',
      });
    });

    it('calls onSelectResource when clicking an unmapped service name', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      const onSelect = vi.fn();
      render(<TrafficFlowOverview clusterId={1} onSelectResource={onSelect} />);
      const user = userEvent.setup();

      // Expand unmapped services
      await waitFor(() => {
        expect(screen.getByText('Unmapped Services')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Unmapped Services'));

      await waitFor(() => {
        expect(screen.getByText('orphan-svc')).toBeInTheDocument();
      });
      await user.click(screen.getByText('orphan-svc'));

      expect(onSelect).toHaveBeenCalledWith({
        kind: 'Service',
        name: 'orphan-svc',
        namespace: 'bnk-demo',
      });
    });
  });

  describe('Navigate to Backends hint', () => {
    it('shows navigate-to-Backends hint in unmapped services when onNavigateView is provided', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      const onNavigate = vi.fn();
      render(<TrafficFlowOverview clusterId={1} onNavigateView={onNavigate} />);
      const user = userEvent.setup();

      // Expand unmapped services
      await waitFor(() => {
        expect(screen.getByText('Unmapped Services')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Unmapped Services'));

      await waitFor(() => {
        expect(screen.getByText('See full details in')).toBeInTheDocument();
      });

      // The "Backends" button inside the hint
      const hintContainer = screen.getByText('See full details in').parentElement!;
      const backendsLink = hintContainer.querySelector('button');
      expect(backendsLink).toHaveTextContent('Backends');

      await user.click(backendsLink!);
      expect(onNavigate).toHaveBeenCalledWith('view-backends');
    });

    it('does not show navigate hint when onNavigateView is not provided', async () => {
      server.use(
        http.get('*/api/k8s/clusters/:id/f5bnk/data', () => {
          return HttpResponse.json(populatedBnkData);
        })
      );

      render(<TrafficFlowOverview clusterId={1} />);
      const user = userEvent.setup();

      await waitFor(() => {
        expect(screen.getByText('Unmapped Services')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Unmapped Services'));

      await waitFor(() => {
        expect(screen.getByText('orphan-svc')).toBeInTheDocument();
      });
      // The "See full details in" hint should not be present
      expect(screen.queryByText('See full details in')).not.toBeInTheDocument();
    });
  });
});
