/**
 * YAML Generators for Policy Builder and Configuration Builder
 *
 * Pure functions that convert builder state into valid K8s YAML manifests.
 * No side effects, no API calls — easily unit-testable.
 */

import type { PolicyBuilderState, PolicyBuilderRule, ConfigBuilderState, ConfigBuilderRoute } from '@/types/f5bnk';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Indent every line of a string by `n` spaces */
function indent(text: string, n: number): string {
  const pad = ' '.repeat(n);
  return text.split('\n').map(line => line ? pad + line : line).join('\n');
}

/** Render a YAML list of strings, or [] if empty */
function yamlStringList(items: string[]): string {
  if (items.length === 0) return '[]';
  return '\n' + items.map(item => `  - ${JSON.stringify(item)}`).join('\n');
}

/** Render a single firewall rule as YAML */
function renderRule(rule: PolicyBuilderRule): string {
  const lines: string[] = [
    `- name: ${rule.name}`,
    `  action: ${rule.action}`,
    `  ipProtocol: ${rule.ipProtocol}`,
    `  source:`,
    `    addressLists: ${yamlStringList(rule.source.addressLists)}`,
    `    addresses: ${yamlStringList(rule.source.addresses)}`,
    `    portLists: ${yamlStringList(rule.source.portLists)}`,
    `    ports: ${yamlStringList(rule.source.ports)}`,
    `  destination:`,
    `    addressLists: ${yamlStringList(rule.destination.addressLists)}`,
    `    addresses: ${yamlStringList(rule.destination.addresses)}`,
    `    portLists: ${yamlStringList(rule.destination.portLists)}`,
    `    ports: ${yamlStringList(rule.destination.ports)}`,
    `  logging: ${rule.logging}`,
  ];
  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Generate YAML manifests for a security policy (BNKSecPolicy + F5BigFwPolicy).
 * Returns a multi-document YAML string separated by "---".
 */
function generateSecurityPolicyYaml(state: PolicyBuilderState): string {
  const rulesYaml = state.firewallRules.length > 0
    ? state.firewallRules.map(renderRule).join('\n')
    : '[]';

  const fwPolicy = [
    `apiVersion: k8s.f5net.com/v1`,
    `kind: F5BigFwPolicy`,
    `metadata:`,
    `  name: ${state.firewallPolicyName}`,
    `  namespace: ${state.namespace}`,
    `spec:`,
    `  rule:`,
    indent(rulesYaml, 2),
  ].join('\n');

  const targetRef = [
    `  - group: gateway.networking.k8s.io`,
    `    kind: Gateway`,
    `    name: ${state.targetGateway}`,
    ...(state.targetListener ? [`    sectionName: ${state.targetListener}`] : []),
  ].join('\n');

  const secPolicy = [
    `apiVersion: gateway.k8s.f5net.com/v1alpha1`,
    `kind: BNKSecPolicy`,
    `metadata:`,
    `  name: ${state.name}`,
    `  namespace: ${state.namespace}`,
    `spec:`,
    `  targetRefs:`,
    targetRef,
    `  policy:`,
    `    firewallPolicy:`,
    `      name: ${state.firewallPolicyName}`,
  ].join('\n');

  return `${fwPolicy}\n---\n${secPolicy}`;
}

/**
 * Generate YAML manifest for a network policy (BNKNetPolicy).
 */
function generateNetworkPolicyYaml(state: PolicyBuilderState): string {
  const targetRef = [
    `  - group: gateway.networking.k8s.io`,
    `    kind: Gateway`,
    `    name: ${state.targetGateway}`,
    ...(state.targetListener ? [`    sectionName: ${state.targetListener}`] : []),
  ].join('\n');

  const extRefs = state.extensionRefs.length > 0
    ? state.extensionRefs.map(ext => [
        `  - group: ${ext.group}`,
        `    kind: ${ext.kind}`,
        `    name: ${ext.name}`,
      ].join('\n')).join('\n')
    : '  []';

  return [
    `apiVersion: gateway.k8s.f5net.com/v1alpha1`,
    `kind: BNKNetPolicy`,
    `metadata:`,
    `  name: ${state.name}`,
    `  namespace: ${state.namespace}`,
    `spec:`,
    `  targetRefs:`,
    targetRef,
    `  extensionRefs:`,
    extRefs,
  ].join('\n');
}

/**
 * Generate complete YAML from the builder state.
 * Returns one or more YAML documents (separated by "---") ready to apply.
 */
export function generatePolicyYaml(state: PolicyBuilderState): string {
  if (state.policyType === 'security') {
    return generateSecurityPolicyYaml(state);
  }
  return generateNetworkPolicyYaml(state);
}

/**
 * Split a multi-document YAML string into individual documents.
 * Each document can be applied separately via the K8s API.
 */
export function splitYamlDocuments(yaml: string): string[] {
  return yaml.split(/^---$/m).map(doc => doc.trim()).filter(Boolean);
}

/**
 * Extract the resource type key from a YAML document for the API call.
 * E.g. "kind: F5BigFwPolicy" → "f5bigfwpolicy"
 */
export function extractResourceType(yamlDoc: string): string | null {
  const match = yamlDoc.match(/^kind:\s*(\S+)/m);
  return match ? match[1].toLowerCase() : null;
}

/**
 * Extract the resource name from a YAML document.
 */
export function extractResourceName(yamlDoc: string): string | null {
  // Match "name:" under "metadata:" section
  const metaMatch = yamlDoc.match(/metadata:\s*\n\s+name:\s*(\S+)/m);
  return metaMatch ? metaMatch[1] : null;
}

// ---------------------------------------------------------------------------
// Rule Templates
// ---------------------------------------------------------------------------

export type RuleTemplate = {
  label: string;
  description: string;
  rule: Omit<PolicyBuilderRule, 'id'>;
};

export const RULE_TEMPLATES: RuleTemplate[] = [
  {
    label: 'Allow HTTP',
    description: 'Accept TCP port 80',
    rule: {
      name: 'allow-http',
      action: 'accept',
      ipProtocol: 'tcp',
      source: { addresses: [], addressLists: [], ports: [], portLists: [] },
      destination: { addresses: [], addressLists: [], ports: ['80'], portLists: [] },
      logging: false,
    },
  },
  {
    label: 'Allow HTTPS',
    description: 'Accept TCP port 443',
    rule: {
      name: 'allow-https',
      action: 'accept',
      ipProtocol: 'tcp',
      source: { addresses: [], addressLists: [], ports: [], portLists: [] },
      destination: { addresses: [], addressLists: [], ports: ['443'], portLists: [] },
      logging: false,
    },
  },
  {
    label: 'Allow DNS',
    description: 'Accept UDP port 53',
    rule: {
      name: 'allow-dns',
      action: 'accept',
      ipProtocol: 'udp',
      source: { addresses: [], addressLists: [], ports: [], portLists: [] },
      destination: { addresses: [], addressLists: [], ports: ['53'], portLists: [] },
      logging: false,
    },
  },
  {
    label: 'Allow SSH',
    description: 'Accept TCP port 22',
    rule: {
      name: 'allow-ssh',
      action: 'accept',
      ipProtocol: 'tcp',
      source: { addresses: [], addressLists: [], ports: [], portLists: [] },
      destination: { addresses: [], addressLists: [], ports: ['22'], portLists: [] },
      logging: false,
    },
  },
  {
    label: 'Drop All',
    description: 'Deny all unmatched traffic',
    rule: {
      name: 'deny-all',
      action: 'drop',
      ipProtocol: 'any',
      source: { addresses: [], addressLists: [], ports: [], portLists: [] },
      destination: { addresses: [], addressLists: [], ports: [], portLists: [] },
      logging: false,
    },
  },
];

export type PolicyTemplate = {
  label: string;
  description: string;
  rules: Array<Omit<PolicyBuilderRule, 'id'>>;
};

export const POLICY_TEMPLATES: PolicyTemplate[] = [
  {
    label: 'Allow Web Traffic',
    description: 'HTTP + HTTPS with deny-all catch-all',
    rules: [
      RULE_TEMPLATES[0].rule,  // Allow HTTP
      RULE_TEMPLATES[1].rule,  // Allow HTTPS
      RULE_TEMPLATES[4].rule,  // Drop All
    ],
  },
  {
    label: 'Allow Internal Only',
    description: 'RFC 1918 private networks, deny all else',
    rules: [
      {
        name: 'allow-internal',
        action: 'accept',
        ipProtocol: 'any',
        source: { addresses: ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'], addressLists: [], ports: [], portLists: [] },
        destination: { addresses: [], addressLists: [], ports: [], portLists: [] },
        logging: false,
      },
      RULE_TEMPLATES[4].rule,  // Drop All
    ],
  },
  {
    label: 'Deny All',
    description: 'Block all traffic (lockdown)',
    rules: [
      RULE_TEMPLATES[4].rule,  // Drop All
    ],
  },
  {
    label: 'Custom (Blank)',
    description: 'Start from scratch',
    rules: [],
  },
];

// ===========================================================================
// Configuration Builder YAML Generation
// ===========================================================================

const ROUTE_API_VERSIONS: Record<ConfigBuilderRoute['kind'], string> = {
  HTTPRoute: 'gateway.networking.k8s.io/v1',
  TCPRoute: 'gateway.networking.k8s.io/v1alpha2',
  UDPRoute: 'gateway.networking.k8s.io/v1alpha2',
  TLSRoute: 'gateway.networking.k8s.io/v1alpha2',
  GRPCRoute: 'gateway.networking.k8s.io/v1',
};

/** Generate Gateway YAML */
function generateGatewayYaml(state: ConfigBuilderState): string {
  const listeners = state.listeners.map(l => {
    const lines = [
      `  - name: ${l.name}`,
      `    port: ${l.port}`,
      `    protocol: ${l.protocol}`,
    ];
    if (l.hostname) lines.push(`    hostname: ${l.hostname}`);
    if ((l.protocol === 'HTTPS' || l.protocol === 'TLS') && l.tls_certificate_ref) {
      lines.push(`    tls:`);
      lines.push(`      mode: Terminate`);
      lines.push(`      certificateRefs:`);
      lines.push(`      - name: ${l.tls_certificate_ref}`);
    }
    return lines.join('\n');
  }).join('\n');

  return [
    `apiVersion: gateway.networking.k8s.io/v1`,
    `kind: Gateway`,
    `metadata:`,
    `  name: ${state.gatewayName}`,
    `  namespace: ${state.namespace}`,
    `spec:`,
    `  gatewayClassName: ${state.gatewayClassName}`,
    `  listeners:`,
    listeners,
  ].join('\n');
}

/** Generate a single Route YAML */
function generateRouteYaml(route: ConfigBuilderRoute, state: ConfigBuilderState): string {
  const apiVersion = ROUTE_API_VERSIONS[route.kind];

  const parentRef = [
    `  - name: ${state.gatewayName}`,
    ...(route.listenerName ? [`    sectionName: ${route.listenerName}`] : []),
  ].join('\n');

  const backends = route.backends.map(b => [
    `    - name: ${b.serviceName}`,
    `      port: ${b.servicePort}`,
    ...(b.weight !== undefined && b.weight !== 1 ? [`      weight: ${b.weight}`] : []),
  ].join('\n')).join('\n');

  if (route.kind === 'HTTPRoute') {
    const hostnames = route.hostnames.length > 0
      ? `  hostnames:\n${route.hostnames.map(h => `  - ${JSON.stringify(h)}`).join('\n')}\n`
      : '';
    const matches = route.matches.length > 0
      ? route.matches.map(m => [
          `    - path:`,
          `        type: ${m.pathType}`,
          `        value: ${m.pathValue}`,
        ].join('\n')).join('\n')
      : `    - path:\n        type: PathPrefix\n        value: /`;

    return [
      `apiVersion: ${apiVersion}`,
      `kind: ${route.kind}`,
      `metadata:`,
      `  name: ${route.name}`,
      `  namespace: ${state.namespace}`,
      `spec:`,
      `  parentRefs:`,
      parentRef,
      hostnames ? hostnames.trimEnd() : null,
      `  rules:`,
      `  - matches:`,
      matches,
      `    backendRefs:`,
      backends,
    ].filter(Boolean).join('\n');
  }

  // TCP/UDP/TLS/GRPC routes — simpler structure
  return [
    `apiVersion: ${apiVersion}`,
    `kind: ${route.kind}`,
    `metadata:`,
    `  name: ${route.name}`,
    `  namespace: ${state.namespace}`,
    `spec:`,
    `  parentRefs:`,
    parentRef,
    `  rules:`,
    `  - backendRefs:`,
    backends,
  ].join('\n');
}

/**
 * Generate all YAML documents from ConfigBuilderState.
 * Returns a multi-document YAML string separated by "---".
 */
export function generateConfigYaml(state: ConfigBuilderState): string {
  const docs: string[] = [];

  // 1. Gateway
  docs.push(generateGatewayYaml(state));

  // 2. Routes
  for (const route of state.routes) {
    docs.push(generateRouteYaml(route, state));
  }

  // 3. Security policy (optional)
  if (state.securityEnabled && state.securityPolicyName && state.firewallPolicyName) {
    const secState: PolicyBuilderState = {
      mode: 'create',
      policyType: 'security',
      name: state.securityPolicyName,
      namespace: state.namespace,
      targetGateway: state.gatewayName,
      targetListener: state.targetListener,
      firewallPolicyName: state.firewallPolicyName,
      firewallRules: state.firewallRules,
      extensionRefs: [],
    };
    docs.push(...splitYamlDocuments(generatePolicyYaml(secState)));
  }

  // 4. Network policy (optional)
  if (state.networkEnabled && state.networkPolicyName && state.networkExtensionRefs.length > 0) {
    const netState: PolicyBuilderState = {
      mode: 'create',
      policyType: 'network',
      name: state.networkPolicyName,
      namespace: state.namespace,
      targetGateway: state.gatewayName,
      targetListener: state.networkTargetListener,
      firewallPolicyName: '',
      firewallRules: [],
      extensionRefs: state.networkExtensionRefs,
    };
    docs.push(generatePolicyYaml(netState));
  }

  return docs.join('\n---\n');
}
