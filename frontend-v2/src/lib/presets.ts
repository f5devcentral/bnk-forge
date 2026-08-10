/**
 * Intent-to-Variable Presets (UX-001 + UX-002)
 *
 * Maps high-level deployment intents ("Dev/Test", "Standard", "Production")
 * to concrete variable values that stack templates expect.
 *
 * Presets are SUGGESTIONS, not constraints. Every value can be overridden
 * by the user in the "I want to customize" advanced panel.
 *
 * Variable names match the stack_templates.json module variable keys exactly.
 */

import type { JsonValue } from '@/types';

// ============================================================================
// Types
// ============================================================================

export interface PresetOption {
  id: string;
  label: string;
  description: string;
  estimatedCost?: string;
  /** Variable overrides keyed by module path → variable name → value */
  variables: Record<string, Record<string, JsonValue>>;
  /** Flat variable overrides (for project-level variable defaults) */
  flatVariables: Record<string, JsonValue>;
}

export interface PresetCategory {
  id: string;
  label: string;
  description: string;
  options: PresetOption[];
}

// ============================================================================
// Network Size Presets
// ============================================================================

export const NETWORK_SIZE_PRESETS: PresetCategory = {
  id: 'network_size',
  label: 'Network Size',
  description: 'VPC and subnet sizing for your infrastructure',
  options: [
    {
      id: 'small',
      label: 'Small (Dev)',
      description: 'Up to 4K addresses — development and testing',
      estimatedCost: '',
      variables: {
        'infra/aws/vpc': {
          vpc_cidr: '10.0.0.0/20',
          public_subnet_cidr: '10.0.1.0/24',
          private_external_subnet_a_cidr: '10.0.2.0/24',
          private_external_subnet_b_cidr: '10.0.3.0/24',
          private_internal_subnet_a_cidr: '10.0.4.0/24',
          private_internal_subnet_b_cidr: '10.0.5.0/24',
        },
      },
      flatVariables: {
        vpc_cidr: '10.0.0.0/20',
        public_subnet_cidr: '10.0.1.0/24',
        private_external_subnet_a_cidr: '10.0.2.0/24',
        private_external_subnet_b_cidr: '10.0.3.0/24',
        private_internal_subnet_a_cidr: '10.0.4.0/24',
        private_internal_subnet_b_cidr: '10.0.5.0/24',
      },
    },
    {
      id: 'medium',
      label: 'Medium (Staging)',
      description: 'Up to 65K addresses — staging and integration',
      estimatedCost: '',
      variables: {
        'infra/aws/vpc': {
          vpc_cidr: '10.0.0.0/16',
          public_subnet_cidr: '10.0.1.0/24',
          private_external_subnet_a_cidr: '10.0.10.0/24',
          private_external_subnet_b_cidr: '10.0.11.0/24',
          private_internal_subnet_a_cidr: '10.0.20.0/24',
          private_internal_subnet_b_cidr: '10.0.21.0/24',
        },
      },
      flatVariables: {
        vpc_cidr: '10.0.0.0/16',
        public_subnet_cidr: '10.0.1.0/24',
        private_external_subnet_a_cidr: '10.0.10.0/24',
        private_external_subnet_b_cidr: '10.0.11.0/24',
        private_internal_subnet_a_cidr: '10.0.20.0/24',
        private_internal_subnet_b_cidr: '10.0.21.0/24',
      },
    },
    {
      id: 'large',
      label: 'Large (Production)',
      description: 'Up to 1M addresses — multi-team production',
      estimatedCost: '',
      variables: {
        'infra/aws/vpc': {
          vpc_cidr: '10.0.0.0/12',
          public_subnet_cidr: '10.0.1.0/24',
          private_external_subnet_a_cidr: '10.0.10.0/24',
          private_external_subnet_b_cidr: '10.0.11.0/24',
          private_internal_subnet_a_cidr: '10.0.20.0/24',
          private_internal_subnet_b_cidr: '10.0.21.0/24',
        },
      },
      flatVariables: {
        vpc_cidr: '10.0.0.0/12',
        public_subnet_cidr: '10.0.1.0/24',
        private_external_subnet_a_cidr: '10.0.10.0/24',
        private_external_subnet_b_cidr: '10.0.11.0/24',
        private_internal_subnet_a_cidr: '10.0.20.0/24',
        private_internal_subnet_b_cidr: '10.0.21.0/24',
      },
    },
  ],
};

// ============================================================================
// Cluster Size Presets
// ============================================================================

export const CLUSTER_SIZE_PRESETS: PresetCategory = {
  id: 'cluster_size',
  label: 'Cluster Size',
  description: 'Kubernetes node count and instance types',
  options: [
    {
      id: 'minimum',
      label: 'Minimum Viable',
      description: '2 nodes, 4 vCPU each — lowest cost',
      estimatedCost: '~$150/mo',
      variables: {
        'infra/aws/eks': {
          node_count: 2,
          instance_type: 't3.large',
          kubernetes_version: '1.31',
        },
        'infra/aws/high-performance-nodes': {
          node_count: 1,
          instance_type: 'c5n.2xlarge',
          f5_bnk_enabled: true,
          tmm_node_count: 1,
        },
      },
      flatVariables: {
        node_count: 2,
        instance_type: 't3.large',
        kubernetes_version: '1.31',
      },
    },
    {
      id: 'standard',
      label: 'Standard',
      description: '3 nodes, 8 vCPU each — balanced performance',
      estimatedCost: '~$450/mo',
      variables: {
        'infra/aws/eks': {
          node_count: 3,
          instance_type: 't3.xlarge',
          kubernetes_version: '1.31',
        },
        'infra/aws/high-performance-nodes': {
          node_count: 2,
          instance_type: 'c5n.4xlarge',
          f5_bnk_enabled: true,
          tmm_node_count: 1,
        },
      },
      flatVariables: {
        node_count: 3,
        instance_type: 't3.xlarge',
        kubernetes_version: '1.31',
      },
    },
    {
      id: 'high_performance',
      label: 'High Performance',
      description: '3 nodes, 16 vCPU each — production workloads',
      estimatedCost: '~$900/mo',
      variables: {
        'infra/aws/eks': {
          node_count: 3,
          instance_type: 'c5n.4xlarge',
          kubernetes_version: '1.31',
        },
        'infra/aws/high-performance-nodes': {
          node_count: 3,
          instance_type: 'c5n.4xlarge',
          f5_bnk_enabled: true,
          tmm_node_count: 2,
        },
      },
      flatVariables: {
        node_count: 3,
        instance_type: 'c5n.4xlarge',
        kubernetes_version: '1.31',
      },
    },
  ],
};

// ============================================================================
// BNK Profile Presets
// ============================================================================

export const BNK_PROFILE_PRESETS: PresetCategory = {
  id: 'bnk_profile',
  label: 'BNK Profile',
  description: 'F5 BIG-IP Next for Kubernetes sizing',
  options: [
    {
      id: 'evaluation',
      label: 'Evaluation',
      description: '1 TMM, basic gateway — try it out',
      estimatedCost: 'License-dependent',
      variables: {
        'bnk/cneinstance': {
          deployment_size: 'Small',
          whole_cluster: true,
          dpu_enabled: false,
          dynamic_routing_enabled: false,
          firewall_acl_enabled: false,
          intelligent_lb_enabled: false,
          telemetry_logging_enabled: true,
          telemetry_metrics_enabled: true,
        },
      },
      flatVariables: {
        deployment_size: 'Small',
      },
    },
    {
      id: 'standard',
      label: 'Standard',
      description: '2 TMM, full gateway with security policies',
      estimatedCost: 'License-dependent',
      variables: {
        'bnk/cneinstance': {
          deployment_size: 'Medium',
          whole_cluster: true,
          dpu_enabled: false,
          dynamic_routing_enabled: true,
          firewall_acl_enabled: true,
          pseudo_cni_enabled: true,
          intelligent_lb_enabled: true,
          telemetry_logging_enabled: true,
          telemetry_metrics_enabled: true,
        },
      },
      flatVariables: {
        deployment_size: 'Medium',
      },
    },
    {
      id: 'high_availability',
      label: 'High Availability',
      description: '3+ TMM, multi-gateway with full feature set',
      estimatedCost: 'License-dependent',
      variables: {
        'bnk/cneinstance': {
          deployment_size: 'Large',
          whole_cluster: true,
          dpu_enabled: false,
          dynamic_routing_enabled: true,
          firewall_acl_enabled: true,
          pseudo_cni_enabled: true,
          core_collection_enabled: true,
          intelligent_lb_enabled: true,
          telemetry_logging_enabled: true,
          telemetry_metrics_enabled: true,
        },
      },
      flatVariables: {
        deployment_size: 'Large',
      },
    },
  ],
};

// ============================================================================
// Environment Size Presets (Composite — used by the wizard)
// ============================================================================

/** Composite preset that combines network, cluster, and BNK presets */
export interface EnvironmentPreset {
  id: string;
  label: string;
  description: string;
  estimatedCost: string;
  estimatedTime: string;
  network: string;   // ID of network preset
  cluster: string;   // ID of cluster preset
  bnk: string;       // ID of BNK preset
}

export const ENVIRONMENT_PRESETS: EnvironmentPreset[] = [
  {
    id: 'dev',
    label: 'Dev / Test',
    description: 'Small network, minimal nodes, evaluation BNK',
    estimatedCost: '~$150/mo',
    estimatedTime: '25-35 min',
    network: 'small',
    cluster: 'minimum',
    bnk: 'evaluation',
  },
  {
    id: 'standard',
    label: 'Standard',
    description: 'Medium network, 3-node cluster, standard BNK',
    estimatedCost: '~$450/mo',
    estimatedTime: '30-40 min',
    network: 'medium',
    cluster: 'standard',
    bnk: 'standard',
  },
  {
    id: 'production',
    label: 'Production',
    description: 'Large network, high-performance cluster, HA BNK',
    estimatedCost: '~$900/mo',
    estimatedTime: '35-45 min',
    network: 'large',
    cluster: 'high_performance',
    bnk: 'high_availability',
  },
];

// ============================================================================
// Stack template mappings for the wizard
// ============================================================================

export type DeploymentIntent = 'full-stack' | 'gateway-only' | 'demo-apps';

export interface DeploymentIntentOption {
  id: DeploymentIntent;
  label: string;
  description: string;
  /** Stack template slug(s) to deploy */
  stackSlugs: string[];
  /** Whether this requires AWS infrastructure */
  requiresAWSInfra: boolean;
}

export const DEPLOYMENT_INTENTS: DeploymentIntentOption[] = [
  {
    id: 'full-stack',
    label: 'Full BNK Stack',
    description: 'Complete F5 BIG-IP Next deployment with all networking components',
    stackSlugs: ['aws-k8s-foundation', 'f5-bnk-2.2'],
    requiresAWSInfra: true,
  },
  {
    id: 'gateway-only',
    label: 'Just a Gateway',
    description: 'BNK on an existing cluster — bring your own Kubernetes',
    stackSlugs: ['bnk-on-k8s'],
    requiresAWSInfra: false,
  },
  {
    id: 'demo-apps',
    label: 'Demo Apps',
    description: 'Try BNK with sample applications — GenAI, traffic routing, observability',
    stackSlugs: ['bnk-demo-apps'],
    requiresAWSInfra: false,
  },
];

// ============================================================================
// Helper: Merge preset variables into a flat overrides map
// ============================================================================

/**
 * Given an environment preset ID, resolves all preset variables into a flat
 * Record<string, JsonValue> suitable for passing as stack variable overrides.
 */
export function resolveEnvironmentPreset(presetId: string): Record<string, JsonValue> {
  const envPreset = ENVIRONMENT_PRESETS.find(p => p.id === presetId);
  if (!envPreset) return {};

  const networkPreset = NETWORK_SIZE_PRESETS.options.find(p => p.id === envPreset.network);
  const clusterPreset = CLUSTER_SIZE_PRESETS.options.find(p => p.id === envPreset.cluster);
  const bnkPreset = BNK_PROFILE_PRESETS.options.find(p => p.id === envPreset.bnk);

  return {
    ...networkPreset?.flatVariables,
    ...clusterPreset?.flatVariables,
    ...bnkPreset?.flatVariables,
  };
}

/**
 * Resolves preset variables structured by module path.
 * Returns Record<modulePath, Record<variableName, value>>.
 */
export function resolvePresetByModule(presetId: string): Record<string, Record<string, JsonValue>> {
  const envPreset = ENVIRONMENT_PRESETS.find(p => p.id === presetId);
  if (!envPreset) return {};

  const networkPreset = NETWORK_SIZE_PRESETS.options.find(p => p.id === envPreset.network);
  const clusterPreset = CLUSTER_SIZE_PRESETS.options.find(p => p.id === envPreset.cluster);
  const bnkPreset = BNK_PROFILE_PRESETS.options.find(p => p.id === envPreset.bnk);

  const result: Record<string, Record<string, JsonValue>> = {};

  const mergeVars = (preset: PresetOption | undefined) => {
    if (!preset) return;
    for (const [modulePath, vars] of Object.entries(preset.variables)) {
      result[modulePath] = { ...result[modulePath], ...vars };
    }
  };

  mergeVars(networkPreset);
  mergeVars(clusterPreset);
  mergeVars(bnkPreset);

  return result;
}
