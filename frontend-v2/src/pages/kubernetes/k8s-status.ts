/**
 * Kubernetes resource status determination logic
 * Extracted from KubernetesV2.tsx (FE-002)
 *
 * Handles status computation for all resource types including:
 * - Pods, Deployments, StatefulSets, DaemonSets, ReplicaSets
 * - Nodes, Services, ConfigMaps, Secrets, Namespaces
 * - PVCs, Ingresses, Gateway API resources
 */

import type { K8sResource, K8sCondition } from '@/types';

/**
 * Determine the display status for a K8s resource based on its type.
 * Handles both camelCase and snake_case field names from the API.
 */
export function getResourceStatus(resource: K8sResource, resourceType: string): string {
  if (resourceType === 'node') {
    const readyCondition = resource.status?.conditions?.find(
      (c: K8sCondition) => c.type === 'Ready'
    );
    return readyCondition?.status === 'True' ? 'Ready' : 'NotReady';
  }

  if (resourceType === 'service' || resourceType === 'configmap' || resourceType === 'secret') {
    return 'Active';
  }

  if (resourceType === 'namespace') {
    return resource.status?.phase || 'Active';
  }

  if (resourceType === 'persistentvolumeclaim') {
    return resource.status?.phase || 'Unknown';
  }

  if (resourceType === 'ingress') {
    return (resource.status?.loadBalancer?.ingress?.length ?? 0) > 0 ? 'Active' : 'Pending';
  }

  if (
    resourceType === 'replicaset' ||
    resourceType === 'statefulset' ||
    resourceType === 'daemonset'
  ) {
    // ReplicaSets/StatefulSets use: ready_replicas, available_replicas, replicas
    // DaemonSets use: number_ready, desired_number_scheduled
    const desired =
      resource.spec?.replicas || resource.status?.desired_number_scheduled || 0;
    const ready =
      resource.status?.ready_replicas || resource.status?.number_ready || 0;
    // ready >= desired → Ready (includes 0/0 = correctly scaled down)
    // ready > 0 but < desired → Partial
    // ready = 0 and desired > 0 → NotReady
    return ready >= desired ? 'Ready' : ready > 0 ? 'Partial' : 'NotReady';
  }

  if (resourceType === 'deployment') {
    const desired = resource.spec?.replicas || 0;
    const ready =
      resource.status?.ready_replicas || resource.status?.readyReplicas || 0;
    return ready >= desired ? 'Ready' : ready > 0 ? 'Partial' : 'NotReady';
  }

  if (resourceType === 'pod') {
    return resource.status?.phase || 'Unknown';
  }

  if (
    [
      'gateway',
      'httproute',
      'grpcroute',
      'tcproute',
      'udproute',
      'tlsroute',
      'gatewayclass',
      'referencegrant',
    ].includes(resourceType)
  ) {
    const acceptedCondition = resource.status?.conditions?.find(
      (c: K8sCondition) => c.type === 'Accepted' || c.type === 'Programmed'
    );
    return acceptedCondition?.status === 'True'
      ? 'Ready'
      : acceptedCondition
        ? 'NotReady'
        : 'Unknown';
  }

  // Fallback for other resource types
  return (
    resource.status?.phase || resource.status?.conditions?.[0]?.type || 'Unknown'
  );
}

/**
 * Return true if a resource should be considered unhealthy for the "only
 * unhealthy" filter. Returns false for kinds where health isn't meaningful
 * (ConfigMaps, Secrets, etc.) — those are always "count-only" and should
 * never be flagged as unhealthy.
 *
 * Mirrors the backend _unhealthy_from_native in routes/k8s/resources.py so
 * the sidebar badge, main table filter, and resource-summary endpoint all
 * agree on what "unhealthy" means.
 */
export function isResourceUnhealthy(resource: K8sResource, resourceType: string): boolean {
  if (resourceType === 'pod') {
    const phase = resource.status?.phase;
    // Succeeded pods (e.g. finished Jobs) and Running pods are healthy.
    return phase !== 'Running' && phase !== 'Succeeded';
  }

  if (
    resourceType === 'deployment' ||
    resourceType === 'statefulset' ||
    resourceType === 'replicaset'
  ) {
    const desired = resource.spec?.replicas;
    const ready =
      resource.status?.ready_replicas || resource.status?.readyReplicas || 0;
    // Scaled-to-0 is intentional, not unhealthy.
    return desired !== undefined && desired !== null && desired > 0 && ready < desired;
  }

  if (resourceType === 'daemonset') {
    const desired =
      resource.status?.desired_number_scheduled ||
      resource.status?.desiredNumberScheduled ||
      0;
    const ready =
      resource.status?.number_ready || resource.status?.numberReady || 0;
    return desired > 0 && ready < desired;
  }

  if (resourceType === 'node') {
    const readyCondition = resource.status?.conditions?.find(
      (c: K8sCondition) => c.type === 'Ready'
    );
    return readyCondition?.status !== 'True';
  }

  // Count-only kinds (services, configmaps, secrets, namespaces, PVCs,
  // ingresses, Gateway API CRDs, cert-manager CRDs) are never flagged as
  // unhealthy by this filter.
  return false;
}
