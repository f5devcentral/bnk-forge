import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  wafGatewayApi,
  type GatewayCreatePayload,
  type GatewayUpdatePayload,
  type HTTPRouteCreatePayload,
  type HTTPRouteUpdatePayload,
} from '@/lib/api/waf-gateway';

const GW_KEYS = {
  topology: (cid: number, ns?: string) => ['waf-gw-topology', cid, ns] as const,
  classes:  (cid: number)              => ['waf-gw-classes',  cid]      as const,
  gateways: (cid: number, ns?: string) => ['waf-gateways',    cid, ns]  as const,
  profiles: (cid: number, ns?: string) => ['waf-sec-profiles', cid, ns] as const,
  routes:   (cid: number, ns?: string) => ['waf-httproutes',  cid, ns]  as const,
  grants:   (cid: number, ns?: string) => ['waf-refgrants',   cid, ns]  as const,
};

const STALE = 20_000;

export function useWafGatewayTopology(clusterId: number | null, namespace?: string) {
  return useQuery({
    queryKey: GW_KEYS.topology(clusterId!, namespace),
    queryFn: () => wafGatewayApi.getTopology(clusterId!, namespace),
    enabled: !!clusterId,
    staleTime: STALE,
  });
}

// ─── GatewayClass ─────────────────────────────────────────────────────────────

export function useGatewayClasses(clusterId: number | null) {
  return useQuery({
    queryKey: GW_KEYS.classes(clusterId!),
    queryFn: () => wafGatewayApi.listGatewayClasses(clusterId!),
    enabled: !!clusterId,
    staleTime: STALE,
  });
}

export function useCreateGatewayClass(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { name: string; controller_name?: string; description?: string }) =>
      wafGatewayApi.createGatewayClass(clusterId, p),
    onSuccess: () => qc.invalidateQueries({ queryKey: GW_KEYS.classes(clusterId) }),
  });
}

export function useDeleteGatewayClass(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => wafGatewayApi.deleteGatewayClass(clusterId, name),
    onSuccess: () => qc.invalidateQueries({ queryKey: GW_KEYS.classes(clusterId) }),
  });
}

// ─── Gateway ──────────────────────────────────────────────────────────────────

export function useGateways(clusterId: number | null, namespace?: string) {
  return useQuery({
    queryKey: GW_KEYS.gateways(clusterId!, namespace),
    queryFn: () => wafGatewayApi.listGateways(clusterId!, namespace),
    enabled: !!clusterId,
    staleTime: STALE,
  });
}

export function useCreateGateway(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: GatewayCreatePayload) => wafGatewayApi.createGateway(clusterId, p),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waf-gateways', clusterId] });
      qc.invalidateQueries({ queryKey: ['waf-gw-topology', clusterId] });
    },
  });
}

export function useUpdateGateway(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, payload }: { name: string; payload: GatewayUpdatePayload }) =>
      wafGatewayApi.updateGateway(clusterId, name, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waf-gateways', clusterId] });
      qc.invalidateQueries({ queryKey: ['waf-gw-topology', clusterId] });
    },
  });
}

export function useDeleteGateway(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, namespace }: { name: string; namespace: string }) =>
      wafGatewayApi.deleteGateway(clusterId, name, namespace),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waf-gateways', clusterId] });
      qc.invalidateQueries({ queryKey: ['waf-gw-topology', clusterId] });
    },
  });
}

// ─── F5BigWebSecurityProfile ──────────────────────────────────────────────────

export function useSecurityProfiles(clusterId: number | null, namespace?: string) {
  return useQuery({
    queryKey: GW_KEYS.profiles(clusterId!, namespace),
    queryFn: () => wafGatewayApi.listSecurityProfiles(clusterId!, namespace),
    enabled: !!clusterId,
    staleTime: STALE,
  });
}

export function useCreateSecurityProfile(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { name: string; namespace: string; policy_name: string }) =>
      wafGatewayApi.createSecurityProfile(clusterId, p),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waf-sec-profiles', clusterId] });
      qc.invalidateQueries({ queryKey: ['waf-gw-topology', clusterId] });
    },
  });
}

export function useUpdateSecurityProfile(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, payload }: { name: string; payload: { namespace: string; policy_name: string } }) =>
      wafGatewayApi.updateSecurityProfile(clusterId, name, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['waf-sec-profiles', clusterId] }),
  });
}

export function useDeleteSecurityProfile(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, namespace }: { name: string; namespace: string }) =>
      wafGatewayApi.deleteSecurityProfile(clusterId, name, namespace),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waf-sec-profiles', clusterId] });
      qc.invalidateQueries({ queryKey: ['waf-gw-topology', clusterId] });
    },
  });
}

// ─── HTTPRoute ────────────────────────────────────────────────────────────────

export function useHTTPRoutes(clusterId: number | null, namespace?: string) {
  return useQuery({
    queryKey: GW_KEYS.routes(clusterId!, namespace),
    queryFn: () => wafGatewayApi.listHTTPRoutes(clusterId!, namespace),
    enabled: !!clusterId,
    staleTime: STALE,
  });
}

export function useCreateHTTPRoute(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: HTTPRouteCreatePayload) => wafGatewayApi.createHTTPRoute(clusterId, p),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waf-httproutes', clusterId] });
      qc.invalidateQueries({ queryKey: ['waf-gw-topology', clusterId] });
    },
  });
}

export function useUpdateHTTPRoute(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, payload }: { name: string; payload: HTTPRouteUpdatePayload }) =>
      wafGatewayApi.updateHTTPRoute(clusterId, name, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['waf-httproutes', clusterId] }),
  });
}

export function useDeleteHTTPRoute(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, namespace }: { name: string; namespace: string }) =>
      wafGatewayApi.deleteHTTPRoute(clusterId, name, namespace),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waf-httproutes', clusterId] });
      qc.invalidateQueries({ queryKey: ['waf-gw-topology', clusterId] });
    },
  });
}

// ─── ReferenceGrant ───────────────────────────────────────────────────────────

export function useReferenceGrants(clusterId: number | null, namespace?: string) {
  return useQuery({
    queryKey: GW_KEYS.grants(clusterId!, namespace),
    queryFn: () => wafGatewayApi.listReferenceGrants(clusterId!, namespace),
    enabled: !!clusterId,
    staleTime: STALE,
  });
}

export function useCreateReferenceGrant(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: Parameters<typeof wafGatewayApi.createReferenceGrant>[1]) =>
      wafGatewayApi.createReferenceGrant(clusterId, p),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waf-refgrants', clusterId] });
      qc.invalidateQueries({ queryKey: ['waf-gw-topology', clusterId] });
    },
  });
}

export function useDeleteReferenceGrant(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, namespace }: { name: string; namespace: string }) =>
      wafGatewayApi.deleteReferenceGrant(clusterId, name, namespace),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['waf-refgrants', clusterId] }),
  });
}
