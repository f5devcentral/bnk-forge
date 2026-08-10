/**
 * Unit tests for policy-yaml.ts — YAML generators for Policy Builder and Configuration Builder.
 *
 * Pure function tests: no DOM, no MSW, no React — just input → output.
 */
import { describe, it, expect } from 'vitest';
import {
  generatePolicyYaml,
  splitYamlDocuments,
  extractResourceType,
  extractResourceName,
  generateConfigYaml,
  RULE_TEMPLATES,
  POLICY_TEMPLATES,
} from '../policy-yaml';
import type {
  PolicyBuilderState,
  PolicyBuilderRule,
  ConfigBuilderState,
} from '@/types/f5bnk';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRule(overrides: Partial<PolicyBuilderRule> = {}): PolicyBuilderRule {
  return {
    id: 'rule-1',
    name: 'test-rule',
    action: 'accept',
    ipProtocol: 'tcp',
    source: { addresses: [], addressLists: [], ports: [], portLists: [] },
    destination: { addresses: [], addressLists: [], ports: ['80'], portLists: [] },
    logging: false,
    ...overrides,
  };
}

function makeSecurityPolicyState(overrides: Partial<PolicyBuilderState> = {}): PolicyBuilderState {
  return {
    mode: 'create',
    policyType: 'security',
    name: 'my-sec-policy',
    namespace: 'default',
    targetGateway: 'my-gateway',
    targetListener: 'http',
    firewallPolicyName: 'my-fw-policy',
    firewallRules: [makeRule()],
    extensionRefs: [],
    ...overrides,
  };
}

function makeNetworkPolicyState(overrides: Partial<PolicyBuilderState> = {}): PolicyBuilderState {
  return {
    mode: 'create',
    policyType: 'network',
    name: 'my-net-policy',
    namespace: 'default',
    targetGateway: 'my-gateway',
    targetListener: '',
    firewallPolicyName: '',
    firewallRules: [],
    extensionRefs: [
      { kind: 'F5BigCneIrule', name: 'my-irule', group: 'k8s.f5net.com' },
    ],
    ...overrides,
  };
}

function makeConfigBuilderState(overrides: Partial<ConfigBuilderState> = {}): ConfigBuilderState {
  return {
    gatewayName: 'test-gw',
    gatewayClassName: 'f5-gateway-class',
    namespace: 'default',
    listeners: [{ name: 'http', protocol: 'HTTP', port: 80 }],
    routes: [
      {
        id: 'r1',
        kind: 'HTTPRoute',
        name: 'test-route',
        listenerName: 'http',
        hostnames: ['example.com'],
        matches: [{ pathType: 'PathPrefix', pathValue: '/' }],
        backends: [{ serviceName: 'my-svc', servicePort: 8080 }],
      },
    ],
    securityEnabled: false,
    securityPolicyName: '',
    firewallPolicyName: '',
    firewallRules: [],
    targetListener: '',
    networkEnabled: false,
    networkPolicyName: '',
    networkExtensionRefs: [],
    networkTargetListener: '',
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// splitYamlDocuments
// ---------------------------------------------------------------------------

describe('splitYamlDocuments()', () => {
  it('should split a multi-document YAML string into separate documents', () => {
    const yaml = 'doc1: a\n---\ndoc2: b\n---\ndoc3: c';
    const docs = splitYamlDocuments(yaml);
    expect(docs).toHaveLength(3);
    expect(docs[0]).toBe('doc1: a');
    expect(docs[1]).toBe('doc2: b');
    expect(docs[2]).toBe('doc3: c');
  });

  it('should handle a single document with no separators', () => {
    const yaml = 'kind: Gateway\nmetadata:\n  name: foo';
    const docs = splitYamlDocuments(yaml);
    expect(docs).toHaveLength(1);
    expect(docs[0]).toBe(yaml);
  });

  it('should filter out empty documents from leading/trailing separators', () => {
    const yaml = '---\nkind: A\n---\n---\nkind: B\n---';
    const docs = splitYamlDocuments(yaml);
    expect(docs).toHaveLength(2);
    expect(docs[0]).toContain('kind: A');
    expect(docs[1]).toContain('kind: B');
  });

  it('should return empty array for empty string', () => {
    expect(splitYamlDocuments('')).toEqual([]);
  });

  it('should return empty array for just separators', () => {
    expect(splitYamlDocuments('---\n---\n---')).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// extractResourceType
// ---------------------------------------------------------------------------

describe('extractResourceType()', () => {
  it('should extract the kind from a YAML document', () => {
    expect(extractResourceType('kind: Gateway\nmetadata:\n  name: foo')).toBe('gateway');
    expect(extractResourceType('kind: HTTPRoute\nspec:\n  rules: []')).toBe('httproute');
    expect(extractResourceType('kind: F5BigFwPolicy\nmetadata:\n  name: x')).toBe('f5bigfwpolicy');
    expect(extractResourceType('kind: BNKSecPolicy\nspec: {}')).toBe('bnksecpolicy');
    expect(extractResourceType('kind: BNKNetPolicy\nspec: {}')).toBe('bnknetpolicy');
  });

  it('should return null when no kind is found', () => {
    expect(extractResourceType('metadata:\n  name: foo')).toBeNull();
    expect(extractResourceType('')).toBeNull();
  });

  it('should handle kind anywhere in the document (not just first line)', () => {
    const yaml = 'apiVersion: v1\nkind: Service\nmetadata:\n  name: x';
    expect(extractResourceType(yaml)).toBe('service');
  });
});

// ---------------------------------------------------------------------------
// extractResourceName
// ---------------------------------------------------------------------------

describe('extractResourceName()', () => {
  it('should extract the name from a YAML document', () => {
    const yaml = 'kind: Gateway\nmetadata:\n  name: my-gateway\nspec: {}';
    expect(extractResourceName(yaml)).toBe('my-gateway');
  });

  it('should return null when no metadata.name exists', () => {
    expect(extractResourceName('kind: Gateway\nspec: {}')).toBeNull();
    expect(extractResourceName('')).toBeNull();
  });

  it('should not match name outside metadata block', () => {
    // "name:" at top level without preceding "metadata:" should not match
    const yaml = 'name: top-level\nkind: Pod';
    expect(extractResourceName(yaml)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// generatePolicyYaml — Security
// ---------------------------------------------------------------------------

describe('generatePolicyYaml() — security', () => {
  it('should generate F5BigFwPolicy + BNKSecPolicy documents', () => {
    const state = makeSecurityPolicyState();
    const yaml = generatePolicyYaml(state);
    const docs = splitYamlDocuments(yaml);

    expect(docs).toHaveLength(2);

    // First doc: F5BigFwPolicy
    expect(docs[0]).toContain('kind: F5BigFwPolicy');
    expect(docs[0]).toContain('name: my-fw-policy');
    expect(docs[0]).toContain('namespace: default');
    expect(docs[0]).toContain('action: accept');
    expect(docs[0]).toContain('ipProtocol: tcp');

    // Second doc: BNKSecPolicy
    expect(docs[1]).toContain('kind: BNKSecPolicy');
    expect(docs[1]).toContain('name: my-sec-policy');
    expect(docs[1]).toContain('name: my-gateway');
    expect(docs[1]).toContain('sectionName: http');
    expect(docs[1]).toContain('name: my-fw-policy');
  });

  it('should omit sectionName when targetListener is empty', () => {
    const state = makeSecurityPolicyState({ targetListener: '' });
    const yaml = generatePolicyYaml(state);
    expect(yaml).not.toContain('sectionName');
  });

  it('should render multiple firewall rules in order', () => {
    const state = makeSecurityPolicyState({
      firewallRules: [
        makeRule({ name: 'allow-http', action: 'accept' }),
        makeRule({ name: 'deny-all', action: 'drop', ipProtocol: 'any', destination: { addresses: [], addressLists: [], ports: [], portLists: [] } }),
      ],
    });
    const yaml = generatePolicyYaml(state);
    const httpIdx = yaml.indexOf('allow-http');
    const denyIdx = yaml.indexOf('deny-all');
    expect(httpIdx).toBeLessThan(denyIdx);
  });

  it('should render source/destination address lists and port lists', () => {
    const state = makeSecurityPolicyState({
      firewallRules: [makeRule({
        source: {
          addresses: ['10.0.0.0/8'],
          addressLists: ['internal-nets'],
          ports: ['443'],
          portLists: ['web-ports'],
        },
        destination: {
          addresses: ['192.168.1.0/24'],
          addressLists: [],
          ports: [],
          portLists: ['app-ports'],
        },
      })],
    });
    const yaml = generatePolicyYaml(state);
    expect(yaml).toContain('"10.0.0.0/8"');
    expect(yaml).toContain('"internal-nets"');
    expect(yaml).toContain('"443"');
    expect(yaml).toContain('"web-ports"');
    expect(yaml).toContain('"192.168.1.0/24"');
    expect(yaml).toContain('"app-ports"');
  });

  it('should render logging flag', () => {
    const state = makeSecurityPolicyState({
      firewallRules: [makeRule({ logging: true })],
    });
    const yaml = generatePolicyYaml(state);
    expect(yaml).toContain('logging: true');
  });

  it('should handle empty firewall rules gracefully', () => {
    const state = makeSecurityPolicyState({ firewallRules: [] });
    const yaml = generatePolicyYaml(state);
    expect(yaml).toContain('kind: F5BigFwPolicy');
    expect(yaml).toContain('kind: BNKSecPolicy');
    // Rule section should be empty list
    expect(yaml).toContain('[]');
  });
});

// ---------------------------------------------------------------------------
// generatePolicyYaml — Network
// ---------------------------------------------------------------------------

describe('generatePolicyYaml() — network', () => {
  it('should generate BNKNetPolicy document', () => {
    const state = makeNetworkPolicyState();
    const yaml = generatePolicyYaml(state);
    const docs = splitYamlDocuments(yaml);

    expect(docs).toHaveLength(1);
    expect(yaml).toContain('kind: BNKNetPolicy');
    expect(yaml).toContain('name: my-net-policy');
    expect(yaml).toContain('namespace: default');
    expect(yaml).toContain('kind: F5BigCneIrule');
    expect(yaml).toContain('name: my-irule');
    expect(yaml).toContain('group: k8s.f5net.com');
  });

  it('should include sectionName when targetListener is set', () => {
    const state = makeNetworkPolicyState({ targetListener: 'https' });
    const yaml = generatePolicyYaml(state);
    expect(yaml).toContain('sectionName: https');
  });

  it('should omit sectionName when targetListener is empty', () => {
    const state = makeNetworkPolicyState({ targetListener: '' });
    const yaml = generatePolicyYaml(state);
    expect(yaml).not.toContain('sectionName');
  });

  it('should render multiple extension refs', () => {
    const state = makeNetworkPolicyState({
      extensionRefs: [
        { kind: 'F5BigCneIrule', name: 'irule-a', group: 'k8s.f5net.com' },
        { kind: 'F5BigCneIrule', name: 'irule-b', group: 'k8s.f5net.com' },
      ],
    });
    const yaml = generatePolicyYaml(state);
    expect(yaml).toContain('name: irule-a');
    expect(yaml).toContain('name: irule-b');
  });
});

// ---------------------------------------------------------------------------
// generateConfigYaml
// ---------------------------------------------------------------------------

describe('generateConfigYaml()', () => {
  it('should generate a Gateway document', () => {
    const state = makeConfigBuilderState();
    const yaml = generateConfigYaml(state);
    const docs = splitYamlDocuments(yaml);

    // Gateway + 1 HTTPRoute = 2 docs
    expect(docs).toHaveLength(2);

    expect(docs[0]).toContain('kind: Gateway');
    expect(docs[0]).toContain('name: test-gw');
    expect(docs[0]).toContain('namespace: default');
    expect(docs[0]).toContain('gatewayClassName: f5-gateway-class');
  });

  it('should include listener details in Gateway', () => {
    const state = makeConfigBuilderState({
      listeners: [
        { name: 'http', protocol: 'HTTP', port: 80 },
        { name: 'https', protocol: 'HTTPS', port: 443, tls_certificate_ref: 'my-tls-secret' },
      ],
    });
    const yaml = generateConfigYaml(state);
    const gwDoc = splitYamlDocuments(yaml)[0];

    expect(gwDoc).toContain('name: http');
    expect(gwDoc).toContain('port: 80');
    expect(gwDoc).toContain('protocol: HTTP');
    expect(gwDoc).toContain('name: https');
    expect(gwDoc).toContain('port: 443');
    expect(gwDoc).toContain('protocol: HTTPS');
    expect(gwDoc).toContain('mode: Terminate');
    expect(gwDoc).toContain('name: my-tls-secret');
  });

  it('should include listener hostname when set', () => {
    const state = makeConfigBuilderState({
      listeners: [
        { name: 'http', protocol: 'HTTP', port: 80, hostname: '*.example.com' },
      ],
    });
    const yaml = generateConfigYaml(state);
    expect(yaml).toContain('hostname: *.example.com');
  });

  it('should generate HTTPRoute with hostnames, matches, and backends', () => {
    const state = makeConfigBuilderState();
    const yaml = generateConfigYaml(state);
    const docs = splitYamlDocuments(yaml);
    const routeDoc = docs[1];

    expect(routeDoc).toContain('kind: HTTPRoute');
    expect(routeDoc).toContain('apiVersion: gateway.networking.k8s.io/v1');
    expect(routeDoc).toContain('name: test-route');
    expect(routeDoc).toContain('sectionName: http');
    expect(routeDoc).toContain('"example.com"');
    expect(routeDoc).toContain('type: PathPrefix');
    expect(routeDoc).toContain('value: /');
    expect(routeDoc).toContain('name: my-svc');
    expect(routeDoc).toContain('port: 8080');
  });

  it('should generate TCPRoute with correct apiVersion', () => {
    const state = makeConfigBuilderState({
      routes: [{
        id: 'r1',
        kind: 'TCPRoute',
        name: 'tcp-route',
        listenerName: 'tcp',
        hostnames: [],
        matches: [],
        backends: [{ serviceName: 'tcp-svc', servicePort: 3306 }],
      }],
    });
    const yaml = generateConfigYaml(state);
    const docs = splitYamlDocuments(yaml);
    const routeDoc = docs[1];

    expect(routeDoc).toContain('kind: TCPRoute');
    expect(routeDoc).toContain('apiVersion: gateway.networking.k8s.io/v1alpha2');
    expect(routeDoc).toContain('name: tcp-svc');
    expect(routeDoc).toContain('port: 3306');
  });

  it('should generate GRPCRoute with correct apiVersion', () => {
    const state = makeConfigBuilderState({
      routes: [{
        id: 'r1',
        kind: 'GRPCRoute',
        name: 'grpc-route',
        listenerName: 'http',
        hostnames: [],
        matches: [],
        backends: [{ serviceName: 'grpc-svc', servicePort: 50051 }],
      }],
    });
    const yaml = generateConfigYaml(state);
    const routeDoc = splitYamlDocuments(yaml)[1];

    expect(routeDoc).toContain('kind: GRPCRoute');
    expect(routeDoc).toContain('apiVersion: gateway.networking.k8s.io/v1');
  });

  it('should generate multiple routes', () => {
    const state = makeConfigBuilderState({
      routes: [
        {
          id: 'r1', kind: 'HTTPRoute', name: 'web', listenerName: 'http',
          hostnames: [], matches: [{ pathType: 'PathPrefix', pathValue: '/web' }],
          backends: [{ serviceName: 'web-svc', servicePort: 80 }],
        },
        {
          id: 'r2', kind: 'TCPRoute', name: 'db', listenerName: 'tcp',
          hostnames: [], matches: [],
          backends: [{ serviceName: 'db-svc', servicePort: 5432 }],
        },
      ],
    });
    const yaml = generateConfigYaml(state);
    const docs = splitYamlDocuments(yaml);

    // Gateway + 2 routes = 3 docs
    expect(docs).toHaveLength(3);
    expect(docs[1]).toContain('kind: HTTPRoute');
    expect(docs[1]).toContain('name: web');
    expect(docs[2]).toContain('kind: TCPRoute');
    expect(docs[2]).toContain('name: db');
  });

  it('should include security policy documents when enabled', () => {
    const state = makeConfigBuilderState({
      securityEnabled: true,
      securityPolicyName: 'sec-pol',
      firewallPolicyName: 'fw-pol',
      firewallRules: [makeRule({ name: 'allow-http' })],
      targetListener: 'http',
    });
    const yaml = generateConfigYaml(state);
    const docs = splitYamlDocuments(yaml);

    // Gateway + 1 route + F5BigFwPolicy + BNKSecPolicy = 4 docs
    expect(docs).toHaveLength(4);
    expect(docs[2]).toContain('kind: F5BigFwPolicy');
    expect(docs[2]).toContain('name: fw-pol');
    expect(docs[3]).toContain('kind: BNKSecPolicy');
    expect(docs[3]).toContain('name: sec-pol');
    expect(docs[3]).toContain('sectionName: http');
  });

  it('should include network policy document when enabled', () => {
    const state = makeConfigBuilderState({
      networkEnabled: true,
      networkPolicyName: 'net-pol',
      networkExtensionRefs: [
        { kind: 'F5BigCneIrule', name: 'my-irule', group: 'k8s.f5net.com' },
      ],
      networkTargetListener: '',
    });
    const yaml = generateConfigYaml(state);
    const docs = splitYamlDocuments(yaml);

    // Gateway + 1 route + BNKNetPolicy = 3 docs
    expect(docs).toHaveLength(3);
    expect(docs[2]).toContain('kind: BNKNetPolicy');
    expect(docs[2]).toContain('name: net-pol');
    expect(docs[2]).toContain('name: my-irule');
  });

  it('should include both security and network policies when both enabled', () => {
    const state = makeConfigBuilderState({
      securityEnabled: true,
      securityPolicyName: 'sec-pol',
      firewallPolicyName: 'fw-pol',
      firewallRules: [makeRule()],
      targetListener: '',
      networkEnabled: true,
      networkPolicyName: 'net-pol',
      networkExtensionRefs: [
        { kind: 'F5BigCneIrule', name: 'irule-1', group: 'k8s.f5net.com' },
      ],
      networkTargetListener: '',
    });
    const yaml = generateConfigYaml(state);
    const docs = splitYamlDocuments(yaml);

    // Gateway + 1 route + F5BigFwPolicy + BNKSecPolicy + BNKNetPolicy = 5 docs
    expect(docs).toHaveLength(5);
    const kinds = docs.map(d => extractResourceType(d));
    expect(kinds).toEqual(['gateway', 'httproute', 'f5bigfwpolicy', 'bnksecpolicy', 'bnknetpolicy']);
  });

  it('should skip security policy when name is empty', () => {
    const state = makeConfigBuilderState({
      securityEnabled: true,
      securityPolicyName: '',  // empty — should skip
      firewallPolicyName: 'fw-pol',
      firewallRules: [makeRule()],
    });
    const yaml = generateConfigYaml(state);
    const docs = splitYamlDocuments(yaml);
    expect(docs).toHaveLength(2); // Gateway + route only
  });

  it('should skip network policy when extensionRefs is empty', () => {
    const state = makeConfigBuilderState({
      networkEnabled: true,
      networkPolicyName: 'net-pol',
      networkExtensionRefs: [],  // empty — should skip
    });
    const yaml = generateConfigYaml(state);
    const docs = splitYamlDocuments(yaml);
    expect(docs).toHaveLength(2); // Gateway + route only
  });

  it('should handle no routes (gateway only)', () => {
    const state = makeConfigBuilderState({ routes: [] });
    const yaml = generateConfigYaml(state);
    const docs = splitYamlDocuments(yaml);

    expect(docs).toHaveLength(1);
    expect(docs[0]).toContain('kind: Gateway');
  });

  it('should generate HTTPRoute without hostnames when empty', () => {
    const state = makeConfigBuilderState({
      routes: [{
        id: 'r1', kind: 'HTTPRoute', name: 'no-hosts', listenerName: 'http',
        hostnames: [],
        matches: [{ pathType: 'Exact', pathValue: '/api' }],
        backends: [{ serviceName: 'api-svc', servicePort: 3000 }],
      }],
    });
    const yaml = generateConfigYaml(state);
    const routeDoc = splitYamlDocuments(yaml)[1];

    expect(routeDoc).not.toContain('hostnames');
    expect(routeDoc).toContain('type: Exact');
    expect(routeDoc).toContain('value: /api');
  });

  it('should render backend weight when specified', () => {
    const state = makeConfigBuilderState({
      routes: [{
        id: 'r1', kind: 'HTTPRoute', name: 'weighted', listenerName: 'http',
        hostnames: [],
        matches: [{ pathType: 'PathPrefix', pathValue: '/' }],
        backends: [
          { serviceName: 'v1', servicePort: 80, weight: 90 },
          { serviceName: 'v2', servicePort: 80, weight: 10 },
        ],
      }],
    });
    const yaml = generateConfigYaml(state);
    expect(yaml).toContain('weight: 90');
    expect(yaml).toContain('weight: 10');
  });

  it('should not render weight: 1 (default)', () => {
    const state = makeConfigBuilderState({
      routes: [{
        id: 'r1', kind: 'HTTPRoute', name: 'default-weight', listenerName: 'http',
        hostnames: [],
        matches: [{ pathType: 'PathPrefix', pathValue: '/' }],
        backends: [{ serviceName: 'svc', servicePort: 80, weight: 1 }],
      }],
    });
    const yaml = generateConfigYaml(state);
    expect(yaml).not.toContain('weight:');
  });
});

// ---------------------------------------------------------------------------
// RULE_TEMPLATES + POLICY_TEMPLATES
// ---------------------------------------------------------------------------

describe('RULE_TEMPLATES', () => {
  it('should have at least 4 templates', () => {
    expect(RULE_TEMPLATES.length).toBeGreaterThanOrEqual(4);
  });

  it('should have unique labels', () => {
    const labels = RULE_TEMPLATES.map(t => t.label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('should have valid actions', () => {
    for (const t of RULE_TEMPLATES) {
      expect(['accept', 'drop', 'reject']).toContain(t.rule.action);
    }
  });

  it('should have valid ipProtocols', () => {
    for (const t of RULE_TEMPLATES) {
      expect(['tcp', 'udp', 'any']).toContain(t.rule.ipProtocol);
    }
  });
});

describe('POLICY_TEMPLATES', () => {
  it('should have at least 3 templates', () => {
    expect(POLICY_TEMPLATES.length).toBeGreaterThanOrEqual(3);
  });

  it('should have a "Custom (Blank)" template with empty rules', () => {
    const blank = POLICY_TEMPLATES.find(t => t.label.includes('Blank') || t.label.includes('Custom'));
    expect(blank).toBeDefined();
    expect(blank!.rules).toHaveLength(0);
  });

  it('should have a web traffic template with HTTP + HTTPS + deny-all', () => {
    const web = POLICY_TEMPLATES.find(t => t.label.includes('Web'));
    expect(web).toBeDefined();
    const actions = web!.rules.map(r => r.action);
    expect(actions).toContain('accept');
    expect(actions).toContain('drop');
  });
});
