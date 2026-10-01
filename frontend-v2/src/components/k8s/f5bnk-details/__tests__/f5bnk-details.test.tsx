import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import type { K8sResource } from '@/types';

// Import all detail components
import { VlanDetail } from '../VlanDetail';
import { FirewallPolicyDetail } from '../FirewallPolicyDetail';
import { FirewallRuleListDetail } from '../FirewallRuleListDetail';
import { SecurityPolicyDetail } from '../SecurityPolicyDetail';
import { NetworkPolicyDetail } from '../NetworkPolicyDetail';
import { iRuleDetail } from '../iRuleDetail';
import { SnatPoolDetail } from '../SnatPoolDetail';
import { AddressListDetail } from '../AddressListDetail';
import { PortListDetail } from '../PortListDetail';
import { CNEInstanceDetail } from '../CNEInstanceDetail';
import { StaticRouteDetail } from '../StaticRouteDetail';
import { EgressDetail } from '../EgressDetail';
import { HslPublisherDetail } from '../HslPublisherDetail';
import { LogProfileDetail } from '../LogProfileDetail';
import { GlobalOptionsDetail } from '../GlobalOptionsDetail';
import { DdosGlobalDetail } from '../DdosGlobalDetail';
import { GatewayClassDetail } from '../GatewayClassDetail';
import { L4RouteDetail } from '../L4RouteDetail';
import { IpamRangeDetail } from '../IpamRangeDetail';
import { BnkGatewayDetail } from '../BnkGatewayDetail';
import { ServiceDetail } from '../ServiceDetail';

// Shared helpers
import { InfoRow, Section, ConditionsTab, getConditionIcon, getConditionColor } from '../shared';

// ---------------------------------------------------------------------------
// Helper to build a minimal K8sResource
// ---------------------------------------------------------------------------

function makeResource(overrides: Partial<K8sResource> = {}): K8sResource {
  return {
    name: 'test-resource',
    kind: 'Unknown',
    apiVersion: 'v1',
    metadata: {
      name: 'test-resource',
      namespace: 'bnk-system',
      uid: 'uid-123',
      creationTimestamp: '2026-01-01T00:00:00Z',
    },
    spec: {},
    status: {},
    ...overrides,
  };
}

// ============================================================================
// shared.tsx
// ============================================================================

describe('shared helpers', () => {
  describe('getConditionColor', () => {
    it('returns token classes per condition status', () => {
      expect(getConditionColor('True')).toBe('text-success');
      expect(getConditionColor('False')).toBe('text-destructive');
      expect(getConditionColor('Unknown')).toBe('text-warning');
      expect(getConditionColor('other')).toBe('text-muted-foreground');
    });
  });

  describe('getConditionIcon', () => {
    it('returns a React element for each status', () => {
      expect(getConditionIcon('True')).toBeTruthy();
      expect(getConditionIcon('False')).toBeTruthy();
      expect(getConditionIcon('Unknown')).toBeTruthy();
      expect(getConditionIcon('other')).toBeTruthy();
    });
  });

  describe('InfoRow', () => {
    it('renders label and value', () => {
      render(<InfoRow label="Protocol" value="TCP" />);
      expect(screen.getByText('Protocol:')).toBeInTheDocument();
      expect(screen.getByText('TCP')).toBeInTheDocument();
    });

    it('returns null for null/undefined/empty values', () => {
      const { container } = render(<InfoRow label="Empty" value={null} />);
      expect(container.innerHTML).toBe('');
    });
  });

  describe('Section', () => {
    it('renders title and children', () => {
      render(<Section title="Config"><span>child-content</span></Section>);
      expect(screen.getByText('Config')).toBeInTheDocument();
      expect(screen.getByText('child-content')).toBeInTheDocument();
    });
  });

  describe('ConditionsTab', () => {
    it('renders conditions with type and status', () => {
      const conditions = [
        { type: 'Ready', status: 'True', reason: 'AllGood', message: 'Resource is ready' },
      ];
      render(<ConditionsTab conditions={conditions} />);
      expect(screen.getByText('Ready')).toBeInTheDocument();
    });

    it('shows empty state when no conditions', () => {
      render(<ConditionsTab conditions={[]} />);
      expect(screen.getByText('No status conditions available')).toBeInTheDocument();
    });
  });
});

// ============================================================================
// VlanDetail
// ============================================================================

describe('VlanDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneVlan',
    spec: {
      name: 'internal-vlan',
      tag: 4094,
      mtu: 1500,
      interfaces: [{ name: 'eth0' }],
      selfip_v4s: ['10.1.1.1/24'],
    },
    status: { conditions: [{ type: 'Ready', status: 'True', reason: 'OK' }] },
  });

  it('renders VLAN configuration fields', () => {
    render(<VlanDetail resource={resource} />);
    expect(screen.getByText('VLAN Configuration')).toBeInTheDocument();
    expect(screen.getByText('internal-vlan')).toBeInTheDocument();
    expect(screen.getByText('4094')).toBeInTheDocument();
    expect(screen.getByText('1500')).toBeInTheDocument();
  });

  it('shows interfaces and self IPs', () => {
    render(<VlanDetail resource={resource} />);
    expect(screen.getByText('eth0')).toBeInTheDocument();
    expect(screen.getByText('10.1.1.1/24')).toBeInTheDocument();
  });
});

// ============================================================================
// FirewallPolicyDetail
// ============================================================================

describe('FirewallPolicyDetail', () => {
  const resource = makeResource({
    kind: 'F5BigFwPolicy',
    spec: {
      description: 'Main policy',
      rule: [
        {
          name: 'allow-https',
          action: 'accept',
          ipProtocol: 'tcp',
          source: { addressLists: ['trusted-nets'] },
          destination: { portLists: ['https-ports'] },
          logging: true,
        },
        {
          name: 'drop-all',
          action: 'drop',
          ipProtocol: 'any',
          logging: false,
        },
      ],
    },
    status: { conditions: [] },
  });

  it('displays rule count in tab', () => {
    render(<FirewallPolicyDetail resource={resource} />);
    expect(screen.getByText('Rules (2)')).toBeInTheDocument();
  });

  it('renders rule names and action badges', () => {
    render(<FirewallPolicyDetail resource={resource} />);
    expect(screen.getByText('allow-https')).toBeInTheDocument();
    expect(screen.getByText('accept')).toBeInTheDocument();
    expect(screen.getByText('drop-all')).toBeInTheDocument();
    expect(screen.getByText('drop')).toBeInTheDocument();
  });

  it('shows empty state when no rules', () => {
    const empty = makeResource({ kind: 'F5BigFwPolicy', spec: {} });
    render(<FirewallPolicyDetail resource={empty} />);
    expect(screen.getByText('Rules (0)')).toBeInTheDocument();
    expect(screen.getByText('No rules defined')).toBeInTheDocument();
  });
});

// ============================================================================
// FirewallRuleListDetail
// ============================================================================

describe('FirewallRuleListDetail', () => {
  const resource = makeResource({
    kind: 'F5BigFwRulelist',
    spec: {
      rule: [{ name: 'rule-1', action: 'accept', ipProtocol: 'tcp', logging: true }],
    },
  });

  it('renders rules tab with count', () => {
    render(<FirewallRuleListDetail resource={resource} />);
    expect(screen.getByText('Rules (1)')).toBeInTheDocument();
    expect(screen.getByText('rule-1')).toBeInTheDocument();
  });

  it('shows empty state when no rules', () => {
    const empty = makeResource({ kind: 'F5BigFwRulelist', spec: {} });
    render(<FirewallRuleListDetail resource={empty} />);
    expect(screen.getByText('No rules in this list')).toBeInTheDocument();
  });
});

// ============================================================================
// NetworkPolicyDetail
// ============================================================================

describe('NetworkPolicyDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneNetworkPolicy',
    spec: {
      extensionRefs: [
        { name: 'my-irule', kind: 'F5BigCneIrule' },
        { name: 'log-profile', kind: 'F5BigCneLogProfile' },
      ],
      targetRefs: [
        { name: 'my-gateway', kind: 'Gateway', sectionName: 'http' },
      ],
    },
    status: {
      ancestors: [],
      descendants: [],
    },
  });

  it('shows extensions and targets tab counts', () => {
    render(<NetworkPolicyDetail resource={resource} />);
    expect(screen.getByText('Extensions (2)')).toBeInTheDocument();
    expect(screen.getByText('Targets (1)')).toBeInTheDocument();
  });

  it('displays extension ref names', () => {
    render(<NetworkPolicyDetail resource={resource} />);
    expect(screen.getByText('my-irule')).toBeInTheDocument();
    expect(screen.getByText('log-profile')).toBeInTheDocument();
  });
});

// ============================================================================
// CNEInstanceDetail
// ============================================================================

describe('CNEInstanceDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneInstance',
    spec: {
      firewallACL: { enabled: true },
      intelligentLB: { enabled: false },
      containerPlatform: 'k8s',
      tmmReplicas: 2,
      networkAttachments: ['macvlan-net1'],
    },
    status: { conditions: [{ type: 'Ready', status: 'True', reason: 'OK' }] },
  });

  it('renders feature toggles', () => {
    render(<CNEInstanceDetail resource={resource} />);
    expect(screen.getByText('Feature Toggles')).toBeInTheDocument();
    expect(screen.getByText('Firewall ACL:')).toBeInTheDocument();
    expect(screen.getByText('Intelligent LB:')).toBeInTheDocument();
  });

  it('displays enabled/disabled badges', () => {
    render(<CNEInstanceDetail resource={resource} />);
    expect(screen.getByText('Enabled')).toBeInTheDocument();
    expect(screen.getByText('Disabled')).toBeInTheDocument();
  });
});

// ============================================================================
// SecurityPolicyDetail
// ============================================================================

describe('SecurityPolicyDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneSecurityPolicy',
    spec: {
      targetRefs: [
        { name: 'my-gw', kind: 'Gateway', sectionName: 'https' },
      ],
      firewallPolicy: { name: 'fw-policy-1', namespace: 'bnk-system' },
    },
  });

  it('renders target gateways with section name', () => {
    render(<SecurityPolicyDetail resource={resource} />);
    expect(screen.getByText('my-gw')).toBeInTheDocument();
    expect(screen.getByText('listener: https')).toBeInTheDocument();
  });

  it('shows firewall policy reference', () => {
    render(<SecurityPolicyDetail resource={resource} />);
    expect(screen.getByText('fw-policy-1')).toBeInTheDocument();
  });
});

// ============================================================================
// AddressListDetail
// ============================================================================

describe('AddressListDetail', () => {
  const resource = makeResource({
    kind: 'F5BigFwAddressList',
    spec: {
      addresses: [{ address: '10.0.0.0/8' }, { address: '192.168.1.0/24' }],
    },
  });

  it('renders addresses with count', () => {
    render(<AddressListDetail resource={resource} />);
    expect(screen.getByText('Addresses (2)')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.0/8')).toBeInTheDocument();
    expect(screen.getByText('192.168.1.0/24')).toBeInTheDocument();
  });

  it('shows empty state when no addresses', () => {
    const empty = makeResource({ kind: 'F5BigFwAddressList', spec: {} });
    render(<AddressListDetail resource={empty} />);
    expect(screen.getByText('No addresses defined')).toBeInTheDocument();
  });
});

// ============================================================================
// PortListDetail
// ============================================================================

describe('PortListDetail', () => {
  const resource = makeResource({
    kind: 'F5BigFwPortList',
    spec: { ports: [{ port: '443' }, { port: '8080' }] },
  });

  it('renders ports with count', () => {
    render(<PortListDetail resource={resource} />);
    expect(screen.getByText('Ports (2)')).toBeInTheDocument();
    expect(screen.getByText('443')).toBeInTheDocument();
    expect(screen.getByText('8080')).toBeInTheDocument();
  });

  it('shows empty state when no ports', () => {
    const empty = makeResource({ kind: 'F5BigFwPortList', spec: {} });
    render(<PortListDetail resource={empty} />);
    expect(screen.getByText('No ports defined')).toBeInTheDocument();
  });
});

// ============================================================================
// SnatPoolDetail
// ============================================================================

describe('SnatPoolDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneSnatPool',
    spec: { members: ['10.10.10.1', '10.10.10.2'] },
  });

  it('renders member count and addresses', () => {
    render(<SnatPoolDetail resource={resource} />);
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('10.10.10.1')).toBeInTheDocument();
    expect(screen.getByText('10.10.10.2')).toBeInTheDocument();
  });
});

// ============================================================================
// iRuleDetail
// ============================================================================

describe('iRuleDetail', () => {
  const IRuleDetail = iRuleDetail; // alias for JSX usage (lowercase function)
  const resource = makeResource({
    kind: 'F5BigCneIrule',
    spec: {
      iRule: 'when HTTP_REQUEST {\n  log local0. "request"\n}\nwhen HTTP_RESPONSE {\n  log local0. "response"\n}',
    },
  });

  it('renders iRule info with line count', () => {
    render(<IRuleDetail resource={resource} />);
    expect(screen.getByText('iRule Info')).toBeInTheDocument();
    // 6 lines in the iRule code (including empty lines from template literal)
    expect(screen.getByText('6')).toBeInTheDocument();
  });

  it('extracts event handlers from code', () => {
    render(<IRuleDetail resource={resource} />);
    expect(screen.getByText('HTTP_REQUEST')).toBeInTheDocument();
    expect(screen.getByText('HTTP_RESPONSE')).toBeInTheDocument();
  });
});

// ============================================================================
// StaticRouteDetail
// ============================================================================

describe('StaticRouteDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneStaticRoute',
    spec: {
      destination: '0.0.0.0/0',
      gateway: '10.1.1.1',
      mtu: 1500,
    },
  });

  it('renders route configuration fields', () => {
    render(<StaticRouteDetail resource={resource} />);
    expect(screen.getByText('Route Configuration')).toBeInTheDocument();
    expect(screen.getByText('0.0.0.0/0')).toBeInTheDocument();
    expect(screen.getByText('10.1.1.1')).toBeInTheDocument();
  });
});

// ============================================================================
// EgressDetail
// ============================================================================

describe('EgressDetail', () => {
  const resource = makeResource({
    kind: 'F5SPKEgress',
    namespace: 'f5-cne-system',
    spec: {
      snatType: 'SRC_TRANS_AUTOMAP',
      egressSnatpool: 'my-snatpool',
      firewallEnforcedPolicy: 'egress-demo-fw',
      logProfile: 'egress-log-profile',
      pseudoCNIConfig: {
        namespaces: ['bnk-egress-demo'],
        appPodInterface: 'eth0',
        vxlan: {
          tmmInterfaceName: 'ext-vlan',
          nodeInterfaceName: 'ens5',
        },
      },
    },
  });

  it('renders egress config fields', () => {
    render(<EgressDetail resource={resource} />);
    expect(screen.getByText('Egress Config')).toBeInTheDocument();
    expect(screen.getByText('SRC_TRANS_AUTOMAP')).toBeInTheDocument();
    expect(screen.getByText('my-snatpool')).toBeInTheDocument();
    expect(screen.getByText('egress-demo-fw')).toBeInTheDocument();
    expect(screen.getByText('egress-log-profile')).toBeInTheDocument();
  });

  it('renders captured namespaces', () => {
    render(<EgressDetail resource={resource} />);
    expect(screen.getByText('Captured Namespaces')).toBeInTheDocument();
    expect(screen.getByText('bnk-egress-demo')).toBeInTheDocument();
  });

  it('renders VXLAN interfaces', () => {
    render(<EgressDetail resource={resource} />);
    expect(screen.getByText('VXLAN')).toBeInTheDocument();
    expect(screen.getByText('ext-vlan')).toBeInTheDocument();
    expect(screen.getByText('ens5')).toBeInTheDocument();
  });

  it('omits captured namespaces and VXLAN sections when absent', () => {
    const minimal = makeResource({ kind: 'F5SPKEgress', spec: { snatType: 'SRC_TRANS_NONE' } });
    render(<EgressDetail resource={minimal} />);
    expect(screen.queryByText('Captured Namespaces')).not.toBeInTheDocument();
    expect(screen.queryByText('VXLAN')).not.toBeInTheDocument();
  });
});

// ============================================================================
// HslPublisherDetail
// ============================================================================

describe('HslPublisherDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneHslPublisher',
    spec: { protocol: 'UDP', pool: 'log-pool', port: 514 },
  });

  it('renders publisher fields', () => {
    render(<HslPublisherDetail resource={resource} />);
    expect(screen.getByText('HSL Publisher')).toBeInTheDocument();
    expect(screen.getByText('UDP')).toBeInTheDocument();
    expect(screen.getByText('log-pool')).toBeInTheDocument();
    expect(screen.getByText('514')).toBeInTheDocument();
  });
});

// ============================================================================
// LogProfileDetail
// ============================================================================

describe('LogProfileDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneLogProfile',
    spec: {
      hslPublisher: 'my-publisher',
      networkSecurity: { enabled: true, rateLimit: 100 },
    },
  });

  it('renders log profile and network security section', () => {
    render(<LogProfileDetail resource={resource} />);
    expect(screen.getByText('Log Profile')).toBeInTheDocument();
    expect(screen.getByText('my-publisher')).toBeInTheDocument();
    expect(screen.getByText('Network Security Logging')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
  });
});

// ============================================================================
// GlobalOptionsDetail
// ============================================================================

describe('GlobalOptionsDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneGlobalOptions',
    spec: { debugLevel: 'info', maxConnections: 10000 },
  });

  it('renders global options with spec fields', () => {
    render(<GlobalOptionsDetail resource={resource} />);
    expect(screen.getByText('Global Options')).toBeInTheDocument();
    expect(screen.getByText('Configuration')).toBeInTheDocument();
    expect(screen.getByText('info')).toBeInTheDocument();
    expect(screen.getByText('10000')).toBeInTheDocument();
  });
});

// ============================================================================
// DdosGlobalDetail
// ============================================================================

describe('DdosGlobalDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneDdosGlobal',
    spec: { enabled: true },
  });

  it('renders DDoS Global Protection section', () => {
    render(<DdosGlobalDetail resource={resource} />);
    expect(screen.getByText('DDoS Global Protection')).toBeInTheDocument();
  });

  it('shows spec fields in configuration section', () => {
    render(<DdosGlobalDetail resource={resource} />);
    expect(screen.getByText('Configuration')).toBeInTheDocument();
    expect(screen.getByText('true')).toBeInTheDocument();
  });
});

// ============================================================================
// GatewayClassDetail
// ============================================================================

describe('GatewayClassDetail', () => {
  const resource = makeResource({
    kind: 'GatewayClass',
    spec: {
      controllerName: 'f5.io/bnk-gateway-controller',
      parametersRef: {
        group: 'gateway.f5.io',
        kind: 'F5BigCneGlobal',
        name: 'global-params',
        namespace: 'bnk-system',
      },
    },
  });

  it('renders controller name and parameters ref', () => {
    render(<GatewayClassDetail resource={resource} />);
    expect(screen.getByText('Gateway Class')).toBeInTheDocument();
    expect(screen.getByText('f5.io/bnk-gateway-controller')).toBeInTheDocument();
    expect(screen.getByText('Parameters Reference')).toBeInTheDocument();
    expect(screen.getByText('global-params')).toBeInTheDocument();
  });
});

// ============================================================================
// L4RouteDetail
// ============================================================================

describe('L4RouteDetail', () => {
  const resource = makeResource({
    kind: 'TLSRoute',
    spec: {
      parentRefs: [{ name: 'my-gateway', sectionName: 'tls' }],
      rules: [
        {
          backendRefs: [
            { name: 'backend-svc', port: 8443, weight: 100 },
          ],
        },
      ],
    },
  });

  it('renders parent gateways and rules count', () => {
    render(<L4RouteDetail resource={resource} />);
    expect(screen.getByText('my-gateway')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument(); // Rules count
  });

  it('shows parent gateway with section name badge', () => {
    render(<L4RouteDetail resource={resource} />);
    expect(screen.getByText('tls')).toBeInTheDocument();
  });
});

// ============================================================================
// IpamRangeDetail
// ============================================================================

describe('IpamRangeDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneIpamRange',
    spec: { startAddress: '10.1.1.10', endAddress: '10.1.1.50', cidr: '10.1.1.0/24' },
  });

  it('renders IPAM range fields', () => {
    render(<IpamRangeDetail resource={resource} />);
    expect(screen.getByText('IPAM Range')).toBeInTheDocument();
    expect(screen.getByText('10.1.1.10')).toBeInTheDocument();
    expect(screen.getByText('10.1.1.50')).toBeInTheDocument();
    expect(screen.getByText('10.1.1.0/24')).toBeInTheDocument();
  });
});

// ============================================================================
// BnkGatewayDetail
// ============================================================================

describe('BnkGatewayDetail', () => {
  const resource = makeResource({
    kind: 'F5BigCneBnkGateway',
    spec: { ipamRange: 'my-range', gatewayRef: { name: 'my-gateway' } },
  });

  it('renders BNK Gateway IPAM details', () => {
    render(<BnkGatewayDetail resource={resource} />);
    expect(screen.getByText('BNK Gateway (IPAM)')).toBeInTheDocument();
    expect(screen.getByText('my-range')).toBeInTheDocument();
    expect(screen.getByText('my-gateway')).toBeInTheDocument();
  });
});

// ============================================================================
// ServiceDetail
// ============================================================================

describe('ServiceDetail', () => {
  const resource = makeResource({
    kind: 'Service',
    spec: {
      type: 'ClusterIP',
      clusterIP: '10.0.0.1',
      ports: [
        { port: 80, targetPort: 8080, protocol: 'TCP', name: 'http' },
        { port: 443, targetPort: 8443, protocol: 'TCP', name: 'https' },
      ],
      selector: { app: 'api', version: 'v1' },
    },
    status: {
      conditions: [{ type: 'Ready', status: 'True', reason: 'Ready', message: 'Endpoints ready' }],
    },
  });

  it('renders service type and cluster IP', () => {
    render(<ServiceDetail resource={resource} />);
    expect(screen.getByText('Service')).toBeInTheDocument();
    expect(screen.getByText('ClusterIP')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.1')).toBeInTheDocument();
  });

  it('renders ports with target ports and protocols', () => {
    render(<ServiceDetail resource={resource} />);
    expect(screen.getByText('80 → 8080')).toBeInTheDocument();
    expect(screen.getByText('443 → 8443')).toBeInTheDocument();
    expect(screen.getByText('http')).toBeInTheDocument();
    expect(screen.getByText('https')).toBeInTheDocument();
    expect(screen.getAllByText('TCP').length).toBe(2);
  });

  it('renders selector labels', () => {
    render(<ServiceDetail resource={resource} />);
    expect(screen.getByText('app=api')).toBeInTheDocument();
    expect(screen.getByText('version=v1')).toBeInTheDocument();
  });

  it('shows status conditions in status tab', async () => {
    render(<ServiceDetail resource={resource} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Status' }));
    expect(screen.getAllByText('Ready').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Endpoints ready')).toBeInTheDocument();
  });
});
