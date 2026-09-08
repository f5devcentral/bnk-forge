import { apiClient } from './client';

const base = (clusterId: number) => `/api/k8s/clusters/${clusterId}/waf`;

// ─── types ────────────────────────────────────────────────────────────────────

export interface K8sCondition {
  type: string;
  status: string;
  reason: string;
  message: string;
  lastTransitionTime?: string;
}

export interface GatewayClass {
  metadata: { name: string; creationTimestamp?: string };
  spec: { controllerName: string; description?: string };
  status?: { conditions?: K8sCondition[] };
}

export interface GatewayListener {
  name: string;
  protocol: string;
  port: number;
  allowedRoutes?: { namespaces?: { from?: string } };
  tls?: { mode?: string; certificateRefs?: { name: string; namespace?: string; kind?: string }[] };
}

export interface Gateway {
  metadata: { name: string; namespace: string; annotations?: Record<string, string>; creationTimestamp?: string };
  spec: { gatewayClassName: string; listeners: GatewayListener[]; addresses?: { type: string; value: string }[] };
  status?: { addresses?: { type: string; value: string }[]; conditions?: K8sCondition[]; listeners?: { name: string; conditions?: K8sCondition[] }[] };
}

export interface SecurityProfile {
  metadata: { name: string; namespace: string; creationTimestamp?: string };
  spec: { policyName: string };
  status?: { conditions?: K8sCondition[] };
}

export interface HTTPRouteParentStatus {
  parentRef: { name: string; namespace?: string; kind?: string };
  controllerName?: string;
  conditions?: K8sCondition[];
}

export interface HTTPRoute {
  metadata: { name: string; namespace: string; creationTimestamp?: string };
  spec: {
    parentRefs: { name: string; namespace?: string; sectionName?: string; group?: string; kind?: string }[];
    hostnames?: string[];
    rules: {
      matches?: { path?: { type: string; value: string }; headers?: { name: string; value: string }[] }[];
      backendRefs: { name: string; port: number; namespace?: string; weight?: number }[];
      filters?: unknown[];
    }[];
  };
  status?: { parents?: HTTPRouteParentStatus[] };
}

export interface ReferenceGrant {
  metadata: { name: string; namespace: string; creationTimestamp?: string };
  spec: {
    from: { group: string; kind: string; namespace: string }[];
    to: { group: string; kind: string; name?: string }[];
  };
}

export interface GatewayTopology {
  gateway_classes: GatewayClass[];
  gateways: Gateway[];
  httproutes: HTTPRoute[];
  security_profiles: SecurityProfile[];
  waf_policies: { metadata: { name: string; namespace: string } }[];
  reference_grants: ReferenceGrant[];
}

// ─── listener request model ───────────────────────────────────────────────────

export interface ListenerRequest {
  name: string;
  protocol: string;
  port: number;
  allowed_routes_from: 'Same' | 'All' | 'Selector';
  tls_mode?: string;
  tls_cert_ref_name?: string;
  tls_cert_ref_namespace?: string;
}

export interface GatewayCreatePayload {
  name: string;
  namespace: string;
  gateway_class_name: string;
  listeners: ListenerRequest[];
  addresses?: string[];
  waf_profile_name?: string;
  annotations?: Record<string, string>;
}

export interface GatewayUpdatePayload {
  namespace: string;
  listeners: ListenerRequest[];
  addresses?: string[];
  waf_profile_name?: string;
  annotations?: Record<string, string>;
}

export interface BackendRefPayload { name: string; port: number; namespace?: string; weight?: number }
export interface RouteMatchPayload { path_type: string; path_value: string; headers?: { name: string; value: string }[] }
export interface RouteRulePayload { matches?: RouteMatchPayload[]; backend_refs: BackendRefPayload[]; filters?: unknown[] }

export interface HTTPRouteCreatePayload {
  name: string;
  namespace: string;
  parent_gateway_name: string;
  parent_gateway_namespace?: string;
  parent_gateway_section_name?: string;
  hostnames?: string[];
  rules: RouteRulePayload[];
}

export interface HTTPRouteUpdatePayload {
  namespace: string;
  parent_gateway_name: string;
  parent_gateway_namespace?: string;
  parent_gateway_section_name?: string;
  hostnames?: string[];
  rules: RouteRulePayload[];
}

// ─── API client ───────────────────────────────────────────────────────────────

export const wafGatewayApi = {
  // topology
  getTopology: (clusterId: number, namespace?: string) =>
    apiClient.get<GatewayTopology>(`${base(clusterId)}/gateway-topology`, { params: namespace ? { namespace } : {} }).then(r => r.data),

  // GatewayClass
  listGatewayClasses: (clusterId: number) =>
    apiClient.get<{ gateway_classes: GatewayClass[] }>(`${base(clusterId)}/gateway-classes`).then(r => r.data.gateway_classes),
  createGatewayClass: (clusterId: number, payload: { name: string; controller_name?: string; description?: string }) =>
    apiClient.post(`${base(clusterId)}/gateway-classes`, payload).then(r => r.data),
  deleteGatewayClass: (clusterId: number, name: string) =>
    apiClient.delete(`${base(clusterId)}/gateway-classes/${name}`).then(r => r.data),

  // Gateway
  listGateways: (clusterId: number, namespace?: string) =>
    apiClient.get<{ gateways: Gateway[] }>(`${base(clusterId)}/gateways`, { params: namespace ? { namespace } : {} }).then(r => r.data.gateways),
  getGateway: (clusterId: number, name: string, namespace: string) =>
    apiClient.get<Gateway>(`${base(clusterId)}/gateways/${name}`, { params: { namespace } }).then(r => r.data),
  createGateway: (clusterId: number, payload: GatewayCreatePayload) =>
    apiClient.post<Gateway>(`${base(clusterId)}/gateways`, payload).then(r => r.data),
  updateGateway: (clusterId: number, name: string, payload: GatewayUpdatePayload) =>
    apiClient.put<Gateway>(`${base(clusterId)}/gateways/${name}`, payload).then(r => r.data),
  deleteGateway: (clusterId: number, name: string, namespace: string) =>
    apiClient.delete(`${base(clusterId)}/gateways/${name}`, { params: { namespace } }).then(r => r.data),

  // F5BigWebSecurityProfile
  listSecurityProfiles: (clusterId: number, namespace?: string) =>
    apiClient.get<{ profiles: SecurityProfile[] }>(`${base(clusterId)}/security-profiles`, { params: namespace ? { namespace } : {} }).then(r => r.data.profiles),
  createSecurityProfile: (clusterId: number, payload: { name: string; namespace: string; policy_name: string }) =>
    apiClient.post<SecurityProfile>(`${base(clusterId)}/security-profiles`, payload).then(r => r.data),
  updateSecurityProfile: (clusterId: number, name: string, payload: { namespace: string; policy_name: string }) =>
    apiClient.put<SecurityProfile>(`${base(clusterId)}/security-profiles/${name}`, payload).then(r => r.data),
  deleteSecurityProfile: (clusterId: number, name: string, namespace: string) =>
    apiClient.delete(`${base(clusterId)}/security-profiles/${name}`, { params: { namespace } }).then(r => r.data),

  // HTTPRoute
  listHTTPRoutes: (clusterId: number, namespace?: string) =>
    apiClient.get<{ routes: HTTPRoute[] }>(`${base(clusterId)}/httproutes`, { params: namespace ? { namespace } : {} }).then(r => r.data.routes),
  createHTTPRoute: (clusterId: number, payload: HTTPRouteCreatePayload) =>
    apiClient.post<HTTPRoute>(`${base(clusterId)}/httproutes`, payload).then(r => r.data),
  updateHTTPRoute: (clusterId: number, name: string, payload: HTTPRouteUpdatePayload) =>
    apiClient.put<HTTPRoute>(`${base(clusterId)}/httproutes/${name}`, payload).then(r => r.data),
  deleteHTTPRoute: (clusterId: number, name: string, namespace: string) =>
    apiClient.delete(`${base(clusterId)}/httproutes/${name}`, { params: { namespace } }).then(r => r.data),

  // ReferenceGrant
  listReferenceGrants: (clusterId: number, namespace?: string) =>
    apiClient.get<{ reference_grants: ReferenceGrant[] }>(`${base(clusterId)}/reference-grants`, { params: namespace ? { namespace } : {} }).then(r => r.data.reference_grants),
  createReferenceGrant: (clusterId: number, payload: { name: string; namespace: string; from_group?: string; from_kind?: string; from_namespace: string; to_group?: string; to_kind?: string; to_name?: string }) =>
    apiClient.post<ReferenceGrant>(`${base(clusterId)}/reference-grants`, payload).then(r => r.data),
  deleteReferenceGrant: (clusterId: number, name: string, namespace: string) =>
    apiClient.delete(`${base(clusterId)}/reference-grants/${name}`, { params: { namespace } }).then(r => r.data),
};
