import type { K8sResource } from '@/types/kubernetes';

/**
 * Backend registry key for a fetched resource. Usually kind.toLowerCase(), but BNK 2.4
 * serves L4Route from gateway.k8s.f5.com under its own key (l4route_24).
 */
export function getResourceTypeKey(resource: Pick<K8sResource, 'kind' | 'apiVersion'>): string {
  const key = resource.kind.toLowerCase();
  if (key === 'l4route' && resource.apiVersion?.startsWith('gateway.k8s.f5.com/')) return 'l4route_24';
  return key;
}
