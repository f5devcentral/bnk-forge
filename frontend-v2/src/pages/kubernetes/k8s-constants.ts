/**
 * Kubernetes resource tree structure and helper functions
 * Extracted from KubernetesV2.tsx for readability.
 */

import {
  Box, Container, Database, Network, Shield, Globe, Lock, Server, Package, FolderOpen,
} from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import { getResourceStatusColor } from '@/lib/status-colors';
import type { K8sResource, K8sContainerStatus } from '@/types';

// Resource tree structure with categories
export const resourceTree = [
  {
    category: 'Workloads',
    icon: Container,
    items: [
      { key: 'pod', label: 'Pods', icon: Box },
      { key: 'deployment', label: 'Deployments', icon: Database },
      { key: 'statefulset', label: 'StatefulSets', icon: Database },
      { key: 'daemonset', label: 'DaemonSets', icon: Server },
      { key: 'replicaset', label: 'ReplicaSets', icon: Database },
    ],
  },
  {
    category: 'Networking',
    icon: Network,
    items: [
      { key: 'service', label: 'Services', icon: Network },
      { key: 'ingress', label: 'Ingresses', icon: Globe },
    ],
  },
  {
    category: 'Gateway API',
    icon: Globe,
    items: [
      { key: 'gatewayclass', label: 'Gateway Classes', icon: Shield },
      { key: 'gateway', label: 'Gateways', icon: Globe },
      { key: 'httproute', label: 'HTTP Routes', icon: Network },
      { key: 'grpcroute', label: 'GRPC Routes', icon: Network },
      { key: 'tcproute', label: 'TCP Routes', icon: Network },
      { key: 'udproute', label: 'UDP Routes', icon: Network },
      { key: 'tlsroute', label: 'TLS Routes', icon: Lock },
      { key: 'l4route', label: 'L4 Routes', icon: Network },
      { key: 'referencegrant', label: 'Reference Grants', icon: Shield },
    ],
  },
  {
    category: 'Config & Storage',
    icon: Shield,
    items: [
      { key: 'configmap', label: 'ConfigMaps', icon: Database },
      { key: 'secret', label: 'Secrets', icon: Lock },
      { key: 'persistentvolumeclaim', label: 'PVCs', icon: Database },
    ],
  },
  {
    category: 'Cluster',
    icon: Server,
    items: [
      { key: 'node', label: 'Nodes', icon: Server },
      { key: 'namespace', label: 'Namespaces', icon: Box },
      { key: 'customresourcedefinition', label: 'CRDs', icon: Database },
      { key: 'storageclass', label: 'Storage Classes', icon: Database },
      { key: 'persistentvolume', label: 'Persistent Volumes', icon: Database },
    ],
  },
  {
    category: 'cert-manager',
    icon: Lock,
    items: [
      { key: 'certificate', label: 'Certificates', icon: Lock },
      { key: 'clusterissuer', label: 'Cluster Issuers', icon: Shield },
      { key: 'issuer', label: 'Issuers', icon: Shield },
    ],
  },
  {
    category: 'Helm',
    icon: Package,
    items: [
      { key: 'helmrelease', label: 'Releases', icon: Package },
      { key: 'helmcharts', label: 'Chart Browser', icon: FolderOpen },
    ],
  },
];

// Helper function alias for backward compatibility
export const calculateAge = formatAge;
export const getStatusColor = getResourceStatusColor;

/** Check if a resource type is a Helm virtual resource (not a real K8s API resource) */
export function isHelmResourceType(resourceType: string): boolean {
  return resourceType === 'helmrelease' || resourceType === 'helmcharts';
}

export function getRestartCount(resource: K8sResource): number {
  const containerStatuses = resource.status?.containerStatuses || resource.status?.container_statuses;
  if (!containerStatuses) return 0;
  return containerStatuses.reduce(
    (sum: number, container: K8sContainerStatus) => sum + (container.restartCount || 0),
    0
  );
}
