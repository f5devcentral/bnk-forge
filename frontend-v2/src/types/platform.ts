export type PlatformProfile = 'generic_onprem' | 'eks' | 'aks' | 'gke' | 'roks' | 'ocp' | 'unknown';

export type PlatformProvider = 'aws' | 'azure' | 'gcp' | 'ibm' | 'baremetal' | 'vmware' | 'other' | 'unknown';

export type ManagementBoundary =
  | 'customer_managed_cluster'
  | 'forge_managed_components_only'
  | 'unknown';

export interface PlatformCapabilities {
  secondary_networks?: boolean | null;
  network_attachment_definitions?: boolean | null;
  sriov?: boolean | null;
  hugepages?: boolean | null;
  gateway_api?: boolean | null;
  openshift_routes?: boolean | null;
  scc?: boolean | null;
  machine_config?: boolean | null;
  operator_framework?: boolean | null;
  cloud_load_balancer?: boolean | null;
}

export interface PlatformConstraints {
  managed_control_plane?: boolean | null;
  cluster_lifecycle_external?: boolean | null;
  operator_required_for_networking?: boolean | null;
  privileged_workloads_require_platform_security_binding?: boolean | null;
  direct_node_package_mutation_not_supported?: boolean | null;
}
