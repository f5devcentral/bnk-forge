/**
 * DPF Use-Case Presets
 *
 * Predefined configurations for common NVIDIA DPF deployment scenarios.
 * Each preset defines the DPUServices, service chains, and service interfaces
 * needed for a specific networking use case.
 */

import type {
  DpfDpuServiceInput,
  DpfServiceChainInput,
  DpfServiceInterfaceInput,
} from './dpf-yaml';

// ---------------------------------------------------------------------------
// Preset definition
// ---------------------------------------------------------------------------

export interface DpfUseCasePreset {
  id: string;
  name: string;
  description: string;
  icon: 'network' | 'cloud' | 'storage' | 'shield' | 'zap';
  services: (namespace: string) => DpfDpuServiceInput[];
  serviceChains: (namespace: string) => DpfServiceChainInput[];
  serviceInterfaces: (namespace: string) => DpfServiceInterfaceInput[];
}

// ---------------------------------------------------------------------------
// HBN (Host-Based Networking)
// ---------------------------------------------------------------------------

const HBN_PRESET: DpfUseCasePreset = {
  id: 'hbn',
  name: 'HBN — Host-Based Networking',
  description:
    'Deploy DOCA HBN for underlay BGP routing on DPU nodes. Provides L3 peering with ToR switches for bare-metal networking.',
  icon: 'network',
  services: (ns) => [
    {
      name: 'hbn',
      namespace: ns,
      repoURL: 'oci://nvcr.io/nvidia/doca',
      chart: 'doca-hbn',
      version: '2.4.0',
      serviceID: 'hbn',
      interfaces: ['p0-rep', 'pf0hpf-rep'],
    },
  ],
  serviceChains: (ns) => [
    {
      name: 'hbn-chain',
      namespace: ns,
      switches: [
        {
          name: 'br-hbn',
          ports: [
            { matchLabels: { 'svc.dpu.nvidia.com/interface': 'p0-rep' } },
            { matchLabels: { 'svc.dpu.nvidia.com/interface': 'pf0hpf-rep' } },
          ],
        },
      ],
    },
  ],
  serviceInterfaces: (ns) => [
    {
      name: 'p0-rep',
      namespace: ns,
      interfaceType: 'representor',
      networkName: 'p0-rep-net',
    },
    {
      name: 'pf0hpf-rep',
      namespace: ns,
      interfaceType: 'representor',
      networkName: 'pf0hpf-rep-net',
    },
  ],
};

// ---------------------------------------------------------------------------
// OVN-Kubernetes
// ---------------------------------------------------------------------------

const OVNK_PRESET: DpfUseCasePreset = {
  id: 'ovnk',
  name: 'OVN-Kubernetes — SDN Overlay',
  description:
    'Deploy OVN-Kubernetes on DPU nodes for software-defined networking. Provides overlay networking with Geneve tunnels and network policies.',
  icon: 'cloud',
  services: (ns) => [
    {
      name: 'ovnk',
      namespace: ns,
      repoURL: 'oci://nvcr.io/nvidia/doca',
      chart: 'doca-ovnk',
      version: '2.4.0',
      serviceID: 'ovnk',
      interfaces: ['p0-rep', 'pf0hpf-rep', 'ovnk-internal'],
    },
  ],
  serviceChains: (ns) => [
    {
      name: 'ovnk-chain',
      namespace: ns,
      switches: [
        {
          name: 'br-ovnk',
          ports: [
            { matchLabels: { 'svc.dpu.nvidia.com/interface': 'p0-rep' } },
            { matchLabels: { 'svc.dpu.nvidia.com/interface': 'pf0hpf-rep' } },
            { matchLabels: { 'svc.dpu.nvidia.com/interface': 'ovnk-internal' } },
          ],
        },
      ],
    },
  ],
  serviceInterfaces: (ns) => [
    {
      name: 'p0-rep',
      namespace: ns,
      interfaceType: 'representor',
      networkName: 'p0-rep-net',
    },
    {
      name: 'pf0hpf-rep',
      namespace: ns,
      interfaceType: 'representor',
      networkName: 'pf0hpf-rep-net',
    },
    {
      name: 'ovnk-internal',
      namespace: ns,
      interfaceType: 'ovs-internal',
    },
  ],
};

// ---------------------------------------------------------------------------
// HBN + OVN-K Combined
// ---------------------------------------------------------------------------

const HBN_OVNK_PRESET: DpfUseCasePreset = {
  id: 'hbn-ovnk',
  name: 'HBN + OVN-K — Combined',
  description:
    'Deploy both HBN (underlay BGP) and OVN-K (overlay SDN) on DPU nodes. Best for environments needing both bare-metal and overlay networking.',
  icon: 'zap',
  services: (ns) => [
    {
      name: 'hbn',
      namespace: ns,
      repoURL: 'oci://nvcr.io/nvidia/doca',
      chart: 'doca-hbn',
      version: '2.4.0',
      serviceID: 'hbn',
      interfaces: ['p0-rep', 'pf0hpf-rep'],
    },
    {
      name: 'ovnk',
      namespace: ns,
      repoURL: 'oci://nvcr.io/nvidia/doca',
      chart: 'doca-ovnk',
      version: '2.4.0',
      serviceID: 'ovnk',
      interfaces: ['p0-rep', 'pf0hpf-rep', 'ovnk-internal'],
    },
  ],
  serviceChains: (ns) => [
    {
      name: 'hbn-ovnk-chain',
      namespace: ns,
      switches: [
        {
          name: 'br-combined',
          ports: [
            { matchLabels: { 'svc.dpu.nvidia.com/interface': 'p0-rep' } },
            { matchLabels: { 'svc.dpu.nvidia.com/interface': 'pf0hpf-rep' } },
            { matchLabels: { 'svc.dpu.nvidia.com/interface': 'ovnk-internal' } },
          ],
        },
      ],
    },
  ],
  serviceInterfaces: (ns) => [
    {
      name: 'p0-rep',
      namespace: ns,
      interfaceType: 'representor',
      networkName: 'p0-rep-net',
    },
    {
      name: 'pf0hpf-rep',
      namespace: ns,
      interfaceType: 'representor',
      networkName: 'pf0hpf-rep-net',
    },
    {
      name: 'ovnk-internal',
      namespace: ns,
      interfaceType: 'ovs-internal',
    },
  ],
};

// ---------------------------------------------------------------------------
// SNAP (Storage)
// ---------------------------------------------------------------------------

const SNAP_PRESET: DpfUseCasePreset = {
  id: 'snap',
  name: 'SNAP — Isolated Storage',
  description:
    'Deploy DOCA SNAP for DPU-offloaded NVMe/virtio-blk storage. Provides isolated storage paths through the DPU with hardware acceleration.',
  icon: 'storage',
  services: (ns) => [
    {
      name: 'snap',
      namespace: ns,
      repoURL: 'oci://nvcr.io/nvidia/doca',
      chart: 'doca-snap',
      version: '2.4.0',
      serviceID: 'snap',
    },
  ],
  serviceChains: () => [],
  serviceInterfaces: () => [],
};

// ---------------------------------------------------------------------------
// Passthrough (minimal)
// ---------------------------------------------------------------------------

const PASSTHROUGH_PRESET: DpfUseCasePreset = {
  id: 'passthrough',
  name: 'Passthrough — No Services',
  description:
    'Provision DPUs with no DOCA services. Use this to set up the DPF infrastructure first and deploy services later.',
  icon: 'shield',
  services: () => [],
  serviceChains: () => [],
  serviceInterfaces: () => [],
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export const DPF_USE_CASE_PRESETS: DpfUseCasePreset[] = [
  HBN_PRESET,
  OVNK_PRESET,
  HBN_OVNK_PRESET,
  SNAP_PRESET,
  PASSTHROUGH_PRESET,
];

export function getDpfPresetById(id: string): DpfUseCasePreset | undefined {
  return DPF_USE_CASE_PRESETS.find((p) => p.id === id);
}

// ---------------------------------------------------------------------------
// Common BFB URLs
// ---------------------------------------------------------------------------

export const COMMON_BFB_IMAGES = [
  {
    name: 'bf3-ubuntu22.04-25.10',
    url: 'https://content.mellanox.com/BlueField/BFB/ubuntu22.04/bf3_25.10.bfb',
    description: 'BlueField-3 Ubuntu 22.04 (DOCA 2.10)',
  },
  {
    name: 'bf3-ubuntu22.04-25.04',
    url: 'https://content.mellanox.com/BlueField/BFB/ubuntu22.04/bf3_25.04.bfb',
    description: 'BlueField-3 Ubuntu 22.04 (DOCA 2.9)',
  },
  {
    name: 'bf2-ubuntu22.04-25.10',
    url: 'https://content.mellanox.com/BlueField/BFB/ubuntu22.04/bf2_25.10.bfb',
    description: 'BlueField-2 Ubuntu 22.04 (DOCA 2.10)',
  },
];
