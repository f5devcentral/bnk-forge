/**
 * DPF (NVIDIA DOCA Platform Framework) types
 *
 * Types derived from the actual backend return shapes in:
 * - routes/k8s/dpf.py (3 GET endpoints)
 * - services/dpf/health.py (analyze_dpf_health)
 * - services/dpf/fetch.py (detect_dpf, fetch_all_dpf_data)
 */

// ── Detect Endpoint (lightweight) ─────────────────────────────────────────

export interface DpfDetectResponse {
  detected: boolean;
  version: string | null;
  operator: {
    configured: boolean;
    ready: boolean;
  };
  devices: {
    total: number;
  };
  dpuclusters: number;
  cluster_id: number;
}

// ── Health Analysis ───────────────────────────────────────────────────────

export interface DpfOperatorHealth {
  configured: boolean;
  ready: boolean;
  version: string | null;
  conditions: DpfCondition[];
}

export interface DpfCondition {
  type: string;
  status: string;
  reason: string;
  message: string;
}

export interface DpfDeviceSummary {
  total: number;
  ready: number;
  byCondition: Record<string, number>;
  byType: Record<string, number>;
}

export interface DpfDpuSummary {
  total: number;
  byPhase: Record<string, number>;
}

export interface DpfClusterInfo {
  name: string;
  namespace: string;
  type: string;
  ready: boolean;
}

export interface DpfClusterSummary {
  total: number;
  ready: number;
  clusters: DpfClusterInfo[];
}

export interface DpfBfbImage {
  name: string;
  url: string;
  ready: boolean;
}

export interface DpfBfbSummary {
  total: number;
  ready: number;
  images: DpfBfbImage[];
}

export interface DpfServiceSummary {
  total: number;
  ready: number;
}

export interface DpfHealthResponse {
  status: 'healthy' | 'partial' | 'degraded' | 'no_devices' | 'not_installed';
  operator: DpfOperatorHealth;
  devices: DpfDeviceSummary;
  dpus: DpfDpuSummary;
  clusters: DpfClusterSummary;
  bfbs: DpfBfbSummary;
  flavors: { total: number };
  dpusets: { total: number };
  services: DpfServiceSummary;
  deployments: { total: number };
  serviceChains: { total: number };
  serviceInterfaces: { total: number };
}

// ── Unified Data Endpoint ─────────────────────────────────────────────────

export interface DpfDataResponse {
  health: DpfHealthResponse;
  resources: Record<string, unknown[]>;
  cluster_id: number;
}

// ── Health-only Endpoint ──────────────────────────────────────────────────

export type DpfHealthEndpointResponse = DpfHealthResponse & {
  cluster_id: number;
};

// ── Raw K8s Resource Shapes (for drill-down views) ────────────────────────
// These match the raw CRD objects returned in DpfDataResponse.resources.
// Fields are nullable because K8s API returns null for absent optional fields.

/** Standard K8s metadata subset used by DPF resources. */
export interface DpfResourceMeta {
  name: string;
  namespace?: string;
  uid?: string;
  creationTimestamp?: string;
  labels?: Record<string, string>;
  annotations?: Record<string, string>;
}

/** Standard K8s condition (used across all DPF status objects). */
export interface DpfK8sCondition {
  type: string;
  status: string;
  reason?: string;
  message?: string;
  lastTransitionTime?: string;
}

/** DPUDevice — physical DPU inventory (provisioning.dpu.nvidia.com/v1alpha1). */
export interface DpfDpuDevice {
  metadata: DpfResourceMeta;
  spec?: {
    bmc?: {
      address?: string;  // BMC IP / Redfish URL
    };
    dpuInterface?: string;
    nodeEffect?: {
      taint?: { key?: string; value?: string; effect?: string };
      drain?: boolean;
    };
  };
  status?: {
    dpuType?: string;       // e.g. "BlueField-3"
    serial?: string;        // device serial number
    pciAddress?: string;    // e.g. "0000:04:00.0"
    mac?: string;           // primary MAC address
    firmware?: {
      bmc?: string;
      uefi?: string;
      bsp?: string;
    };
    conditions?: DpfK8sCondition[];
    nodeName?: string;      // host node where device is found
    phase?: string;
  };
}

/** DPUCluster — DPU cluster control plane (provisioning.dpu.nvidia.com/v1alpha1).
 *  Aligned with upstream Go type: api/provisioning/v1alpha1/dpucluster_types.go */
export interface DpfDpuCluster {
  metadata: DpfResourceMeta;
  spec?: {
    type?: string;                  // "kamaji" | "static" | ISV prefix
    maxNodes?: number;              // default 1000, min 1, max 1000
    kubeconfig?: string;            // secret name (for static clusters, immutable)
    clusterEndpoint?: {
      keepalived?: {
        vip?: string;              // virtual IP for the control plane
        virtualRouterID?: number;  // VRRP ID (1-255, required for keepalived)
        interface?: string;        // network interface for VIP
        nodeSelector?: Record<string, string>; // subset of CP nodes for keepalived
      };
    };
  };
  status?: {
    conditions?: DpfK8sCondition[];
    phase?: string;                 // Pending | Creating | Ready | NotReady | Failed
    version?: string;               // K8s control-plane version (observed)
    nodesCount?: number;            // number of DPUs assigned to cluster
  };
}

/** DPU — individual DPU lifecycle object (created by DPUSet controller). */
export interface DpfDpu {
  metadata: DpfResourceMeta;
  spec?: {
    dpuDevice?: string;   // name of the DPUDevice
    bfb?: string;         // name of the BFB
    dpuFlavor?: string;   // name of the DPUFlavor
    dpuSet?: string;      // owning DPUSet
  };
  status?: {
    phase?: string;       // "Initializing" | "Node-effect" | "DPU-provisioning" | "Ready" | "Error" | "Deleting"
    conditions?: DpfK8sCondition[];
  };
}

/** DPUSet — desired state for a group of DPUs (provisioning.dpu.nvidia.com/v1alpha1).
 *  Aligned with upstream Go type: api/provisioning/v1alpha1/dpuset_types.go */
export interface DpfDpuSet {
  metadata: DpfResourceMeta;
  spec?: {
    dpuNodeSelector?: DpfLabelSelector;      // selects DPUNodes (LabelSelector)
    dpuSelector?: Record<string, string>;    // selects DPU devices (DEPRECATED — use dpuDeviceSelector)
    dpuDeviceSelector?: DpfLabelSelector;    // selects DPUDevices (replaces dpuSelector)
    dpuTemplate?: {
      annotations?: Record<string, string>;
      spec?: {
        bfb?: {
          name?: string;                     // BFB image name
        };
        dpuFlavor?: string;                  // DPUFlavor name (required)
        secureBoot?: boolean;                // UEFI Secure Boot
        nodeEffect?: DpfNodeEffect;          // effect on host node during provisioning
        cluster?: {                          // target DPUCluster (via label selector)
          nodeLabels?: Record<string, string>;
          selector?: DpfLabelSelector;
        };
      };
    };
    strategy?: {
      type?: string;                         // "RollingUpdate" | "OnDelete" (default OnDelete)
      rollingUpdate?: {
        maxUnavailable?: number | string;    // deprecated, removal in v26.4.0
      };
    };
  };
  status?: {
    dpuStatistics?: Record<string, number>;  // map of DPUPhase → count
    conditions?: DpfK8sCondition[];
    observedGeneration?: number;
  };
}

/** K8s LabelSelector — matchLabels + matchExpressions. */
export interface DpfLabelSelector {
  matchLabels?: Record<string, string>;
  matchExpressions?: Array<{
    key: string;
    operator: string;  // "In" | "NotIn" | "Exists" | "DoesNotExist"
    values?: string[];
  }>;
}

/** NodeEffect — effect on host K8s node during DPU provisioning.
 *  Exactly one of: drain, taint, noEffect, customAction, customLabel, hold. */
export interface DpfNodeEffect {
  drain?: boolean;
  taint?: { key?: string; value?: string; effect?: string };
  noEffect?: boolean;
  customAction?: string;                     // ConfigMap name with pod YAML
  customLabel?: Record<string, string>;
  hold?: boolean;                            // wait for external removal of annotation
  force?: boolean;                           // skip sync wait + maxUnavailable checks
  applyOnLabelChange?: boolean;              // re-trigger node effect on label changes
  nodeMaintenanceAdditionalRequestors?: string[];
}

/** BFB — BlueField Boot image (provisioning.dpu.nvidia.com/v1alpha1). */
export interface DpfBfb {
  metadata: DpfResourceMeta;
  spec?: {
    url?: string;                            // BFB image download URL
    fileName?: string;                       // target filename
  };
  status?: {
    phase?: string;                          // "Downloading" | "Ready" | "Error"
    conditions?: DpfK8sCondition[];
  };
}

/** DPUFlavor — config template for DPU system-level settings (provisioning.dpu.nvidia.com/v1alpha1).
 *  Aligned with upstream Go type: api/provisioning/v1alpha1/dpuflavor_types.go
 *  NOTE: DPUFlavor spec is immutable once created. */
export interface DpfDpuFlavor {
  metadata: DpfResourceMeta;
  spec?: {
    grub?: {
      kernelParameters?: string[];           // kernel boot params
    };
    sysctl?: {
      parameters?: string[];                 // sysctl key=value pairs
    };
    nvconfig?: DpfNvConfig[];                // firmware/device-specific config (per-device or wildcard)
    ovs?: {
      rawConfigScript?: string;              // raw OVS config script (NOTE: upstream JSON key is rawConfigScript, not rawConfig)
    };
    bfcfgParameters?: string[];              // BF config parameters (NOTE: upstream JSON key is bfcfgParameters, not bfCfgParameters)
    configFiles?: DpfConfigFile[];           // files to write on the DPU
    containerdConfig?: {
      registryEndpoint?: string;             // private container registry for air-gapped envs
    };
    dpuResources?: Record<string, string>;   // minimum resources needed for BFB with this flavor
    systemReservedResources?: Record<string, string>; // resources consumed by OS/OVS/DPF system
    dpuMode?: 'dpu' | 'zero-trust' | 'nic'; // DPU operating mode
    hostNetworkInterfaceConfigs?: DpfHostNetworkInterfaceConfig[]; // host-side NIC config
  };
}

/** NVConfig — firmware-level device settings for a DPU port. */
export interface DpfNvConfig {
  device?: string;                           // "*" | "p0" | "P0" | "p1" | "P1"
  parameters?: string[];                     // KEY=VALUE format
}

/** ConfigFile — file to write on the DPU filesystem. */
export interface DpfConfigFile {
  path?: string;
  operation?: 'override' | 'append';
  raw?: string;                              // file content
  permissions?: string;                      // e.g. "0644"
}

/** Host-side network interface configuration. */
export interface DpfHostNetworkInterfaceConfig {
  portNumber: number;                        // 0 or 1
  mtu?: number;                              // 1280-9216
  dhcp?: boolean;
  nvconfig?: DpfNvConfig;                    // per-port NVConfig
}

// =============================================================================
// SERVICE RESOURCES (svc.dpu.nvidia.com/v1alpha1)
// =============================================================================

/** DPUService — deploy a Helm chart to DPU nodes via ArgoCD (svc.dpu.nvidia.com/v1alpha1). */
export interface DpfDpuService {
  metadata: DpfResourceMeta;
  spec?: {
    helmChart?: {
      source?: {
        repoURL?: string;                    // OCI/HTTP chart repo
        chart?: string;                      // chart name within repo
        version?: string;                    // chart version
        path?: string;                       // path within repo (git)
      };
      values?: Record<string, unknown>;      // Helm values override
    };
    serviceDaemonSet?: {
      labels?: Record<string, string>;
      annotations?: Record<string, string>;
      nodeSelector?: Record<string, string>;
    };
    serviceID?: string;                      // stable service identifier
    interfaces?: string[];                   // interface names this service uses
  };
  status?: {
    conditions?: DpfK8sCondition[];
  };
}

/** DPUDeployment — coordinated deployment group of DPUServices + DPUSets (svc.dpu.nvidia.com/v1alpha1). */
export interface DpfDpuDeployment {
  metadata: DpfResourceMeta;
  spec?: {
    services?: Record<string, {              // named service templates
      helmChart?: {
        source?: {
          repoURL?: string;
          chart?: string;
          version?: string;
        };
        values?: Record<string, unknown>;
      };
      serviceID?: string;
      interfaces?: string[];
    }>;
    dpuSets?: Record<string, {               // named DPUSet templates
      dpuSelector?: Record<string, string>;
      dpuTemplate?: {
        spec?: {
          bfb?: { name?: string };
          dpuFlavor?: string;
        };
      };
    }>;
    serviceChains?: Record<string, {         // named service chain templates
      switches?: DpfOvsSwitch[];
    }>;
  };
  status?: {
    conditions?: DpfK8sCondition[];
    observedGeneration?: number;
  };
}

/** DPUServiceChain — OVS flows for traffic steering between service functions (svc.dpu.nvidia.com/v1alpha1). */
export interface DpfDpuServiceChain {
  metadata: DpfResourceMeta;
  spec?: {
    template?: {
      spec?: {
        switches?: DpfOvsSwitch[];
      };
    };
    nodeSelector?: Record<string, string>;
  };
  status?: {
    conditions?: DpfK8sCondition[];
  };
}

/** DPUServiceInterface — OVS port definition for service function chains (svc.dpu.nvidia.com/v1alpha1). */
export interface DpfDpuServiceInterface {
  metadata: DpfResourceMeta;
  spec?: {
    template?: {
      spec?: {
        interfaceType?: string;              // "vhost-user" | "representor" | "ovs-internal"
        network?: {
          name?: string;                     // NetworkAttachmentDefinition ref
          namespace?: string;
        };
        vlan?: {
          vlanId?: number;
        };
        ipam?: {
          matchLabels?: Record<string, string>;
        };
      };
    };
    nodeSelector?: Record<string, string>;
  };
  status?: {
    conditions?: DpfK8sCondition[];
  };
}

/** DPUServiceIPAM — IP address management for DPU service chains (svc.dpu.nvidia.com/v1alpha1). */
export interface DpfDpuServiceIpam {
  metadata: DpfResourceMeta;
  spec?: {
    ipv4Subnet?: string;                     // CIDR
    ipv4Gateway?: string;
    ipv4Pool?: {
      start?: string;
      end?: string;
    };
  };
  status?: {
    conditions?: DpfK8sCondition[];
  };
}

/** DPUServiceCredentialRequest — cross-cluster auth: host ↔ DPU cluster (svc.dpu.nvidia.com/v1alpha1). */
export interface DpfDpuServiceCredentialRequest {
  metadata: DpfResourceMeta;
  spec?: {
    secretName?: string;                     // target secret to populate
    serviceAccountName?: string;             // SA to authenticate as
    duration?: string;                       // token validity (e.g. "24h")
    type?: string;                           // credential type
  };
  status?: {
    conditions?: DpfK8sCondition[];
    expirationTimestamp?: string;            // ISO 8601
  };
}

/** ServiceChain — per-DPU realized service chain (svc.dpu.nvidia.com/v1alpha1). */
export interface DpfServiceChain {
  metadata: DpfResourceMeta;
  spec?: {
    node?: string;                           // DPU node hosting this chain
    switches?: DpfOvsSwitch[];
  };
  status?: {
    conditions?: DpfK8sCondition[];
  };
}

/** ServiceInterface — per-DPU realized service interface (svc.dpu.nvidia.com/v1alpha1). */
export interface DpfServiceInterface {
  metadata: DpfResourceMeta;
  spec?: {
    node?: string;                           // DPU node hosting this interface
    interfaceType?: string;
    network?: {
      name?: string;
      namespace?: string;
    };
    vlan?: {
      vlanId?: number;
    };
    serviceName?: string;                    // owning DPUService name
    mac?: string;                            // allocated MAC address
  };
  status?: {
    conditions?: DpfK8sCondition[];
    interfaceName?: string;                  // realized OS interface name
  };
}

// ── Shared sub-types for service chain definitions ──

/** OVS switch definition within a service chain. */
export interface DpfOvsSwitch {
  name?: string;
  ports?: DpfOvsSwitchPort[];
}

/** OVS port within a switch — connects service functions in a chain. */
export interface DpfOvsSwitchPort {
  serviceInterface?: {
    matchLabels?: Record<string, string>;
  };
}
