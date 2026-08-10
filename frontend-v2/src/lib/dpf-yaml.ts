/**
 * DPF YAML Generators
 *
 * Pure functions that convert wizard state into valid K8s YAML manifests
 * for NVIDIA DPF (Data Processing Framework) resources.
 *
 * No side effects, no API calls — easily unit-testable.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DpfOperatorConfigInput {
  name: string;
  namespace: string;
  mode: 'host-trusted' | 'zero-trust';
}

export interface DpfDpuClusterInput {
  name: string;
  namespace: string;
  type: 'kamaji' | 'static';
  maxNodes?: number;
  /** Keepalived VIP address (Kamaji clusters) */
  vip?: string;
  /** VRRP virtual router ID (1-255, required when VIP is set) */
  virtualRouterID?: number;
  /** Network interface for the VIP */
  keepalivedInterface?: string;
}

export interface DpfBfbInput {
  name: string;
  namespace: string;
  url: string;
  fileName?: string;
}

export interface DpfDpuFlavorInput {
  name: string;
  namespace: string;
  kernelParameters?: string[];
  sysctlParameters?: string[];
  /** NVConfig firmware settings — array of {device, parameters} */
  nvconfig?: Array<{ device?: string; parameters: string[] }>;
  ovsRawConfigScript?: string;
  bfcfgParameters?: string[];
  /** Files to write to the DPU filesystem */
  configFiles?: Array<{ path: string; operation?: 'override' | 'append'; raw: string; permissions?: string }>;
  /** Private container registry endpoint (air-gapped) */
  containerdRegistryEndpoint?: string;
  /** Minimum resources for this flavor */
  dpuResources?: Record<string, string>;
  /** Resources reserved by the system (OS, OVS, etc.) */
  systemReservedResources?: Record<string, string>;
  /** DPU operating mode */
  dpuMode?: 'dpu' | 'zero-trust' | 'nic';
}

export interface DpfDpuSetInput {
  name: string;
  namespace: string;
  bfbName: string;
  dpuFlavorName: string;
  /** DPUNodeSelector — label selector for host DPUNodes */
  dpuNodeSelector?: Record<string, string>;
  /** DPUDeviceSelector — label selector for DPUDevice resources (replaces deprecated dpuSelector) */
  dpuDeviceSelector?: Record<string, string>;
  /** Target DPUCluster — matched via cluster.selector.matchLabels on DPUCluster labels */
  clusterSelector?: Record<string, string>;
  /** Update strategy: "OnDelete" (default) or "RollingUpdate" */
  strategy?: 'RollingUpdate' | 'OnDelete';
  maxUnavailable?: number | string;
  /** Node effect during provisioning: drain (default), noEffect, taint, hold */
  nodeEffect?: 'drain' | 'noEffect' | 'taint' | 'hold';
  /** Enable UEFI Secure Boot on DPUs */
  secureBoot?: boolean;
  /** Node labels to add to DPU cluster nodes */
  clusterNodeLabels?: Record<string, string>;
}

export interface DpfDpuServiceInput {
  name: string;
  namespace: string;
  repoURL: string;
  chart?: string;
  version?: string;
  path?: string;
  values?: Record<string, unknown>;
  serviceID?: string;
  interfaces?: string[];
}

export interface DpfServiceChainInput {
  name: string;
  namespace: string;
  switches: Array<{
    name: string;
    ports: Array<{
      matchLabels: Record<string, string>;
    }>;
  }>;
  nodeSelector?: Record<string, string>;
}

export interface DpfServiceInterfaceInput {
  name: string;
  namespace: string;
  interfaceType: string;
  networkName?: string;
  networkNamespace?: string;
  vlanId?: number;
  nodeSelector?: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Indent every line of a string by n spaces */
function indent(text: string, n: number): string {
  const pad = ' '.repeat(n);
  return text
    .split('\n')
    .map((line) => (line ? pad + line : line))
    .join('\n');
}

/** Render YAML for Record<string, string> as key: value pairs */
function renderLabels(labels: Record<string, string>, indentLevel: number): string {
  return Object.entries(labels)
    .map(([k, v]) => `${' '.repeat(indentLevel)}${k}: "${v}"`)
    .join('\n');
}

// ---------------------------------------------------------------------------
// DPFOperatorConfig
// ---------------------------------------------------------------------------

export function generateDpfOperatorConfigYaml(input: DpfOperatorConfigInput): string {
  const installInterface =
    input.mode === 'zero-trust' ? 'installViaRedfish' : 'installViaHostAgent';

  return `apiVersion: operator.dpu.nvidia.com/v1alpha1
kind: DPFOperatorConfig
metadata:
  name: ${input.name}
  namespace: ${input.namespace}
spec:
  provisioningController:
    ${installInterface}: {}
  imagePullSecrets: []`;
}

// ---------------------------------------------------------------------------
// DPUCluster
// ---------------------------------------------------------------------------

export function generateDpuClusterYaml(input: DpfDpuClusterInput): string {
  const lines: string[] = [
    `apiVersion: provisioning.dpu.nvidia.com/v1alpha1`,
    `kind: DPUCluster`,
    `metadata:`,
    `  name: ${input.name}`,
    `  namespace: ${input.namespace}`,
    `spec:`,
    `  type: ${input.type}`,
  ];

  if (input.maxNodes != null) {
    lines.push(`  maxNodes: ${input.maxNodes}`);
  }
  if (input.type === 'kamaji' && input.vip) {
    lines.push(`  clusterEndpoint:`);
    lines.push(`    keepalived:`);
    lines.push(`      vip: "${input.vip}"`);
    if (input.virtualRouterID != null) {
      lines.push(`      virtualRouterID: ${input.virtualRouterID}`);
    }
    if (input.keepalivedInterface) {
      lines.push(`      interface: "${input.keepalivedInterface}"`);
    }
  }

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// BFB
// ---------------------------------------------------------------------------

export function generateBfbYaml(input: DpfBfbInput): string {
  const lines: string[] = [
    `apiVersion: provisioning.dpu.nvidia.com/v1alpha1`,
    `kind: BFB`,
    `metadata:`,
    `  name: ${input.name}`,
    `  namespace: ${input.namespace}`,
    `spec:`,
    `  url: "${input.url}"`,
  ];

  if (input.fileName) {
    lines.push(`  fileName: "${input.fileName}"`);
  }

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// DPUFlavor
// ---------------------------------------------------------------------------

export function generateDpuFlavorYaml(input: DpfDpuFlavorInput): string {
  const lines: string[] = [
    `apiVersion: provisioning.dpu.nvidia.com/v1alpha1`,
    `kind: DPUFlavor`,
    `metadata:`,
    `  name: ${input.name}`,
    `  namespace: ${input.namespace}`,
    `spec: {}`,
  ];

  const specLines: string[] = [];

  if (input.dpuMode) {
    specLines.push(`  dpuMode: ${input.dpuMode}`);
  }

  if (input.kernelParameters && input.kernelParameters.length > 0) {
    specLines.push(`  grub:`);
    specLines.push(`    kernelParameters:`);
    for (const p of input.kernelParameters) {
      specLines.push(`      - "${p}"`);
    }
  }

  if (input.sysctlParameters && input.sysctlParameters.length > 0) {
    specLines.push(`  sysctl:`);
    specLines.push(`    parameters:`);
    for (const p of input.sysctlParameters) {
      specLines.push(`      - "${p}"`);
    }
  }

  if (input.nvconfig && input.nvconfig.length > 0) {
    specLines.push(`  nvconfig:`);
    for (const nv of input.nvconfig) {
      specLines.push(`    - device: "${nv.device ?? '*'}"`);
      specLines.push(`      parameters:`);
      for (const p of nv.parameters) {
        specLines.push(`        - "${p}"`);
      }
    }
  }

  if (input.ovsRawConfigScript) {
    specLines.push(`  ovs:`);
    specLines.push(`    rawConfigScript: |`);
    specLines.push(indent(input.ovsRawConfigScript, 6));
  }

  if (input.bfcfgParameters && input.bfcfgParameters.length > 0) {
    specLines.push(`  bfcfgParameters:`);
    for (const p of input.bfcfgParameters) {
      specLines.push(`    - "${p}"`);
    }
  }

  if (input.configFiles && input.configFiles.length > 0) {
    specLines.push(`  configFiles:`);
    for (const f of input.configFiles) {
      specLines.push(`    - path: "${f.path}"`);
      if (f.operation) specLines.push(`      operation: ${f.operation}`);
      if (f.permissions) specLines.push(`      permissions: "${f.permissions}"`);
      specLines.push(`      raw: |`);
      specLines.push(indent(f.raw, 8));
    }
  }

  if (input.containerdRegistryEndpoint) {
    specLines.push(`  containerdConfig:`);
    specLines.push(`    registryEndpoint: "${input.containerdRegistryEndpoint}"`);
  }

  if (input.dpuResources && Object.keys(input.dpuResources).length > 0) {
    specLines.push(`  dpuResources:`);
    specLines.push(renderLabels(input.dpuResources, 4));
  }

  if (input.systemReservedResources && Object.keys(input.systemReservedResources).length > 0) {
    specLines.push(`  systemReservedResources:`);
    specLines.push(renderLabels(input.systemReservedResources, 4));
  }

  if (specLines.length > 0) {
    // Replace the `spec: {}` with actual spec content
    lines[lines.length - 1] = `spec:`;
    lines.push(...specLines);
  }

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// DPUSet
// ---------------------------------------------------------------------------

export function generateDpuSetYaml(input: DpfDpuSetInput): string {
  const lines: string[] = [
    `apiVersion: provisioning.dpu.nvidia.com/v1alpha1`,
    `kind: DPUSet`,
    `metadata:`,
    `  name: ${input.name}`,
    `  namespace: ${input.namespace}`,
    `spec:`,
  ];

  // Strategy (before dpuTemplate per upstream ordering)
  if (input.strategy) {
    lines.push(`  strategy:`);
    lines.push(`    type: ${input.strategy}`);
    if (input.strategy === 'RollingUpdate' && input.maxUnavailable != null) {
      lines.push(`    rollingUpdate:`);
      lines.push(`      maxUnavailable: ${input.maxUnavailable}`);
    }
  }

  // DPUNodeSelector (was incorrectly "nodeSelector" — fixed to match upstream JSON key)
  if (input.dpuNodeSelector && Object.keys(input.dpuNodeSelector).length > 0) {
    lines.push(`  dpuNodeSelector:`);
    lines.push(`    matchLabels:`);
    lines.push(renderLabels(input.dpuNodeSelector, 6));
  }

  // DPUDeviceSelector (replaces deprecated dpuSelector)
  if (input.dpuDeviceSelector && Object.keys(input.dpuDeviceSelector).length > 0) {
    lines.push(`  dpuDeviceSelector:`);
    lines.push(`    matchLabels:`);
    lines.push(renderLabels(input.dpuDeviceSelector, 6));
  }

  // DPU Template
  lines.push(`  dpuTemplate:`);
  lines.push(`    spec:`);
  lines.push(`      bfb:`);
  lines.push(`        name: ${input.bfbName}`);
  lines.push(`      dpuFlavor: ${input.dpuFlavorName}`);

  // Secure Boot
  if (input.secureBoot) {
    lines.push(`      secureBoot: true`);
  }

  // Node effect (default: drain)
  if (input.nodeEffect && input.nodeEffect !== 'drain') {
    lines.push(`      nodeEffect:`);
    switch (input.nodeEffect) {
      case 'noEffect':
        lines.push(`        noEffect: true`);
        break;
      case 'hold':
        lines.push(`        hold: true`);
        break;
      case 'taint':
        lines.push(`        taint:`);
        lines.push(`          key: dpu-provisioning`);
        lines.push(`          effect: NoSchedule`);
        break;
    }
  } else {
    // Default: drain (matches upstream default)
    lines.push(`      nodeEffect:`);
    lines.push(`        drain: true`);
  }

  // Cluster selector (was incorrectly "dpuCluster: name" — fixed to upstream cluster.selector)
  if (input.clusterSelector && Object.keys(input.clusterSelector).length > 0) {
    lines.push(`      cluster:`);
    lines.push(`        selector:`);
    lines.push(`          matchLabels:`);
    lines.push(renderLabels(input.clusterSelector, 12));
    if (input.clusterNodeLabels && Object.keys(input.clusterNodeLabels).length > 0) {
      lines.push(`        nodeLabels:`);
      lines.push(renderLabels(input.clusterNodeLabels, 10));
    }
  }

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// DPUService
// ---------------------------------------------------------------------------

export function generateDpuServiceYaml(input: DpfDpuServiceInput): string {
  const lines: string[] = [
    `apiVersion: svc.dpu.nvidia.com/v1alpha1`,
    `kind: DPUService`,
    `metadata:`,
    `  name: ${input.name}`,
    `  namespace: ${input.namespace}`,
    `spec:`,
    `  helmChart:`,
    `    source:`,
    `      repoURL: "${input.repoURL}"`,
  ];

  if (input.chart) {
    lines.push(`      chart: "${input.chart}"`);
  }
  if (input.version) {
    lines.push(`      version: "${input.version}"`);
  }
  if (input.path) {
    lines.push(`      path: "${input.path}"`);
  }

  if (input.values && Object.keys(input.values).length > 0) {
    lines.push(`    values:`);
    lines.push(indent(renderValuesYaml(input.values), 6));
  }

  if (input.serviceID) {
    lines.push(`  serviceID: "${input.serviceID}"`);
  }

  if (input.interfaces && input.interfaces.length > 0) {
    lines.push(`  interfaces:`);
    for (const iface of input.interfaces) {
      lines.push(`    - "${iface}"`);
    }
  }

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// DPUServiceChain
// ---------------------------------------------------------------------------

export function generateDpuServiceChainYaml(input: DpfServiceChainInput): string {
  const lines: string[] = [
    `apiVersion: svc.dpu.nvidia.com/v1alpha1`,
    `kind: DPUServiceChain`,
    `metadata:`,
    `  name: ${input.name}`,
    `  namespace: ${input.namespace}`,
    `spec:`,
    `  template:`,
    `    spec:`,
    `      switches:`,
  ];

  for (const sw of input.switches) {
    lines.push(`        - name: ${sw.name}`);
    if (sw.ports.length > 0) {
      lines.push(`          ports:`);
      for (const port of sw.ports) {
        lines.push(`            - serviceInterface:`);
        lines.push(`                matchLabels:`);
        for (const [k, v] of Object.entries(port.matchLabels)) {
          lines.push(`                  ${k}: "${v}"`);
        }
      }
    }
  }

  if (input.nodeSelector && Object.keys(input.nodeSelector).length > 0) {
    lines.push(`  nodeSelector:`);
    lines.push(renderLabels(input.nodeSelector, 4));
  }

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// DPUServiceInterface
// ---------------------------------------------------------------------------

export function generateDpuServiceInterfaceYaml(
  input: DpfServiceInterfaceInput
): string {
  const lines: string[] = [
    `apiVersion: svc.dpu.nvidia.com/v1alpha1`,
    `kind: DPUServiceInterface`,
    `metadata:`,
    `  name: ${input.name}`,
    `  namespace: ${input.namespace}`,
    `spec:`,
    `  template:`,
    `    spec:`,
    `      interfaceType: ${input.interfaceType}`,
  ];

  if (input.networkName) {
    lines.push(`      network:`);
    lines.push(`        name: "${input.networkName}"`);
    if (input.networkNamespace) {
      lines.push(`        namespace: "${input.networkNamespace}"`);
    }
  }

  if (input.vlanId != null) {
    lines.push(`      vlan:`);
    lines.push(`        vlanId: ${input.vlanId}`);
  }

  if (input.nodeSelector && Object.keys(input.nodeSelector).length > 0) {
    lines.push(`  nodeSelector:`);
    lines.push(renderLabels(input.nodeSelector, 4));
  }

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// Composite: Generate all resources for a complete DPF setup
// ---------------------------------------------------------------------------

export interface DpfSetupState {
  mode: 'host-trusted' | 'zero-trust';
  namespace: string;
  clusterName: string;
  clusterType: 'kamaji' | 'static';
  maxNodes: number;
  vip: string;
  virtualRouterID: number;
  keepalivedInterface: string;
  bfbName: string;
  bfbUrl: string;
  dpuFlavorName: string;
  dpuFlavorMode: 'dpu' | 'zero-trust' | 'nic' | '';
  /** Kernel boot parameters (GRUB) — e.g., "hugepagesz=2M hugepages=1024" */
  kernelParameters: string[];
  /** Runtime sysctl tuning — e.g., "net.core.rmem_max=16777216" */
  sysctlParameters: string[];
  /** Air-gapped private container registry endpoint */
  containerdRegistryEndpoint: string;
  /** NVConfig firmware parameters — e.g., "NUM_OF_VFS=16", "SRIOV_EN=1" */
  nvconfigParameters: string[];
  /** BlueField config tool parameters */
  bfcfgParameters: string[];
  /** Custom OVS configuration script (shell) */
  ovsRawConfigScript: string;
  dpuSetName: string;
  nodeEffect: 'drain' | 'noEffect' | 'taint' | 'hold';
  secureBoot: boolean;
  strategy: 'OnDelete' | 'RollingUpdate';
  /** Max DPUs unavailable during RollingUpdate */
  maxUnavailable: string;
  /** Label selector to target specific host nodes */
  dpuNodeSelector: Record<string, string>;
  services: DpfDpuServiceInput[];
  serviceChains: DpfServiceChainInput[];
  serviceInterfaces: DpfServiceInterfaceInput[];
}

export function generateAllDpfYaml(state: DpfSetupState): string {
  const docs: string[] = [];

  // 1. DPFOperatorConfig
  docs.push(
    generateDpfOperatorConfigYaml({
      name: 'dpf-operator-config',
      namespace: state.namespace,
      mode: state.mode,
    })
  );

  // 2. DPUCluster
  docs.push(
    generateDpuClusterYaml({
      name: state.clusterName,
      namespace: state.namespace,
      type: state.clusterType,
      maxNodes: state.maxNodes || undefined,
      vip: state.vip || undefined,
      virtualRouterID: state.virtualRouterID || undefined,
      keepalivedInterface: state.keepalivedInterface || undefined,
    })
  );

  // 3. BFB
  docs.push(
    generateBfbYaml({
      name: state.bfbName,
      namespace: state.namespace,
      url: state.bfbUrl,
    })
  );

  // 4. DPUFlavor (if named, or if any flavor settings are configured)
  const hasFlavorSettings =
    state.dpuFlavorName ||
    state.dpuFlavorMode ||
    state.kernelParameters.length > 0 ||
    state.sysctlParameters.length > 0 ||
    state.containerdRegistryEndpoint ||
    state.nvconfigParameters.length > 0 ||
    state.bfcfgParameters.length > 0 ||
    state.ovsRawConfigScript;

  if (hasFlavorSettings) {
    const flavorNm = state.dpuFlavorName || `${state.dpuSetName}-flavor`;
    docs.push(
      generateDpuFlavorYaml({
        name: flavorNm,
        namespace: state.namespace,
        dpuMode: state.dpuFlavorMode || undefined,
        kernelParameters: state.kernelParameters.length > 0 ? state.kernelParameters : undefined,
        sysctlParameters: state.sysctlParameters.length > 0 ? state.sysctlParameters : undefined,
        containerdRegistryEndpoint: state.containerdRegistryEndpoint || undefined,
        nvconfig: state.nvconfigParameters.length > 0
          ? [{ device: '*', parameters: state.nvconfigParameters }]
          : undefined,
        bfcfgParameters: state.bfcfgParameters.length > 0 ? state.bfcfgParameters : undefined,
        ovsRawConfigScript: state.ovsRawConfigScript || undefined,
      })
    );
  }

  // 5. DPUSet — uses cluster.selector to target the DPUCluster by label
  const flavorName = hasFlavorSettings
    ? (state.dpuFlavorName || `${state.dpuSetName}-flavor`)
    : (state.dpuFlavorName || 'default');
  docs.push(
    generateDpuSetYaml({
      name: state.dpuSetName,
      namespace: state.namespace,
      bfbName: state.bfbName,
      dpuFlavorName: flavorName,
      clusterSelector: { 'dpu.nvidia.com/cluster': state.clusterName },
      nodeEffect: state.nodeEffect || 'drain',
      secureBoot: state.secureBoot || undefined,
      strategy: state.strategy || 'OnDelete',
      maxUnavailable: state.strategy === 'RollingUpdate' && state.maxUnavailable
        ? state.maxUnavailable
        : undefined,
      dpuNodeSelector: Object.keys(state.dpuNodeSelector).length > 0
        ? state.dpuNodeSelector
        : undefined,
    })
  );

  // 6. Services
  for (const svc of state.services) {
    docs.push(generateDpuServiceYaml(svc));
  }

  // 7. Service Chains
  for (const chain of state.serviceChains) {
    docs.push(generateDpuServiceChainYaml(chain));
  }

  // 8. Service Interfaces
  for (const iface of state.serviceInterfaces) {
    docs.push(generateDpuServiceInterfaceYaml(iface));
  }

  return docs.join('\n---\n');
}

// ---------------------------------------------------------------------------
// Split + extract helpers (for apply flow)
// ---------------------------------------------------------------------------

export function splitYamlDocuments(yaml: string): string[] {
  return yaml
    .split(/^---$/m)
    .map((doc) => doc.trim())
    .filter((doc) => doc.length > 0);
}

export function extractResourceType(yaml: string): string | null {
  const kindMatch = yaml.match(/^kind:\s*(.+)$/m);
  if (!kindMatch) return null;
  const kind = kindMatch[1].trim();

  // Map Kind → resource registry key
  const kindToType: Record<string, string> = {
    DPFOperatorConfig: 'dpfoperatorconfig',
    DPUCluster: 'dpucluster',
    BFB: 'bfb',
    DPUFlavor: 'dpuflavor',
    DPUSet: 'dpuset',
    DPUService: 'dpuservice',
    DPUDeployment: 'dpudeployment',
    DPUServiceChain: 'dpuservicechain',
    DPUServiceInterface: 'dpuserviceinterface',
    DPUServiceIPAM: 'dpuserviceipam',
    DPUServiceCredentialRequest: 'dpuservicecredreq',
    ServiceChain: 'servicechain',
    ServiceInterface: 'serviceinterface',
  };

  return kindToType[kind] ?? null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Simple object → YAML serializer for Helm values (one level deep). */
function renderValuesYaml(obj: Record<string, unknown>, depth: number = 0): string {
  const pad = ' '.repeat(depth * 2);
  const lines: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
      lines.push(`${pad}${key}:`);
      lines.push(renderValuesYaml(value as Record<string, unknown>, depth + 1));
    } else if (Array.isArray(value)) {
      lines.push(`${pad}${key}:`);
      for (const item of value) {
        lines.push(`${pad}  - ${JSON.stringify(item)}`);
      }
    } else if (typeof value === 'string') {
      lines.push(`${pad}${key}: "${value}"`);
    } else {
      lines.push(`${pad}${key}: ${value}`);
    }
  }
  return lines.join('\n');
}
