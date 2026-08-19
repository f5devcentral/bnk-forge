/**
 * React Query hooks for the WAF Policy Manager.
 *
 * Backend routes perform generic CRD CRUD against appprotect.f5.com/v1
 * resources (APPolicy, APLogConf, APSignatures, APUserSig) — see
 * docs/WAF_POLICY_MANAGER_DESIGN.md.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAppMutation } from '@/hooks/lib/useAppMutation';
import { wafPoliciesApi } from '@/lib/api/waf-policies';
import { queryKeys } from '@/lib/queryKeys';
import { POLL_INTERVALS } from '@/lib/constants';
import { notify, notifyError } from '@/lib/notify';
import type {
  APPolicyResource,
  APLogConfResource,
  APUserSigResource,
  WafPolicyCreateRequest,
  WafPolicyUpdateRequest,
  WafLogConfCreateRequest,
  WafLogConfUpdateRequest,
  WafUserSigCreateRequest,
  WafUserSigUpdateRequest,
  WafSignaturesUpdateRequest,
} from '@/types';

// ---------------------------------------------------------------------------
// APPolicy — list / single (with bundle-state polling) / create / update / delete
// ---------------------------------------------------------------------------

export function useWafPolicies(clusterId: number, namespace?: string, options?: { enabled?: boolean; autoRefresh?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.wafPolicies(clusterId, namespace),
    queryFn: () => wafPoliciesApi.listPolicies(clusterId, { namespace }),
    enabled: options?.enabled !== false && !!clusterId,
    refetchInterval: options?.autoRefresh !== false ? 10_000 : false,
  });
}

/** Polls while the bundle is still compiling (pending/processing); stops once ready/invalid. */
export function useWafPolicy(clusterId: number, name: string, namespace: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.wafPolicy(clusterId, name, namespace),
    queryFn: () => wafPoliciesApi.getPolicy(clusterId, name, { namespace }),
    enabled: options?.enabled !== false && !!clusterId && !!name,
    refetchInterval: (query) => {
      const state = (query.state.data as APPolicyResource | undefined)?.status?.bundle?.state;
      return state === 'pending' || state === 'processing' ? POLL_INTERVALS.FAST : false;
    },
  });
}

export function useCreateWafPolicy(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<APPolicyResource, Error, WafPolicyCreateRequest>({
    mutationFn: (data) => wafPoliciesApi.createPolicy(clusterId, data),
    onSuccess: (data) => {
      notify({ title: 'WAF Policy Created', message: `${data.metadata.name} created — compile pending`, severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafPolicies(clusterId) });
    },
    onError: (error) => notifyError(error, 'creating WAF policy'),
  });
}

export function useUpdateWafPolicy(clusterId: number, name: string) {
  const queryClient = useQueryClient();
  return useAppMutation<APPolicyResource, Error, WafPolicyUpdateRequest>({
    mutationFn: (data) => wafPoliciesApi.updatePolicy(clusterId, name, data),
    onSuccess: (data) => {
      notify({ title: 'WAF Policy Updated', message: `${data.metadata.name} updated — recompile pending`, severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafPolicies(clusterId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafPolicy(clusterId, name, data.metadata.namespace ?? '') });
    },
    onError: (error) => notifyError(error, 'updating WAF policy'),
  });
}

export function useDeleteWafPolicy(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<{ message: string }, Error, { name: string; namespace: string }>({
    mutationFn: ({ name, namespace }) => wafPoliciesApi.deletePolicy(clusterId, name, { namespace }),
    onSuccess: (_data, variables) => {
      notify({ title: 'WAF Policy Deleted', message: `${variables.name} deleted`, severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafPolicies(clusterId) });
    },
    onError: (error) => notifyError(error, 'deleting WAF policy'),
  });
}

// ---------------------------------------------------------------------------
// APLogConf
// ---------------------------------------------------------------------------

export function useWafLogConfs(clusterId: number, namespace?: string, options?: { enabled?: boolean; autoRefresh?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.wafLogConfs(clusterId, namespace),
    queryFn: () => wafPoliciesApi.listLogConfs(clusterId, { namespace }),
    enabled: options?.enabled !== false && !!clusterId,
    refetchInterval: options?.autoRefresh !== false ? 10_000 : false,
  });
}

export function useCreateWafLogConf(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<unknown, Error, WafLogConfCreateRequest>({
    mutationFn: (data) => wafPoliciesApi.createLogConf(clusterId, data),
    onSuccess: () => {
      notify({ title: 'Log Profile Created', severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafLogConfs(clusterId) });
    },
    onError: (error) => notifyError(error, 'creating log profile'),
  });
}

export function useUpdateWafLogConf(clusterId: number, name: string) {
  const queryClient = useQueryClient();
  return useAppMutation<APLogConfResource, Error, WafLogConfUpdateRequest>({
    mutationFn: (data) => wafPoliciesApi.updateLogConf(clusterId, name, data),
    onSuccess: () => {
      notify({ title: 'Log Profile Updated', severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafLogConfs(clusterId) });
    },
    onError: (error) => notifyError(error, 'updating log profile'),
  });
}

export function useDeleteWafLogConf(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<{ message: string }, Error, { name: string; namespace: string }>({
    mutationFn: ({ name, namespace }) => wafPoliciesApi.deleteLogConf(clusterId, name, { namespace }),
    onSuccess: (_data, variables) => {
      notify({ title: 'Log Profile Deleted', message: variables.name, severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafLogConfs(clusterId) });
    },
    onError: (error) => notifyError(error, 'deleting log profile'),
  });
}

// ---------------------------------------------------------------------------
// APSignatures — singleton per namespace
// ---------------------------------------------------------------------------

export function useWafSignatures(clusterId: number, namespace: string, options?: { enabled?: boolean; autoRefresh?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.wafSignatures(clusterId, namespace),
    queryFn: () => wafPoliciesApi.getSignatures(clusterId, { namespace }),
    enabled: options?.enabled !== false && !!clusterId && !!namespace,
    refetchInterval: options?.autoRefresh !== false ? 10_000 : false,
  });
}

export function useUpsertWafSignatures(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<unknown, Error, WafSignaturesUpdateRequest>({
    mutationFn: (data) => wafPoliciesApi.upsertSignatures(clusterId, data),
    onSuccess: (_data, variables) => {
      notify({ title: 'Signature Settings Saved', severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafSignatures(clusterId, variables.namespace) });
    },
    onError: (error) => notifyError(error, 'saving signature settings'),
  });
}

export function useDeleteWafSignatures(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<{ message: string }, Error, { namespace: string }>({
    mutationFn: ({ namespace }) => wafPoliciesApi.deleteSignatures(clusterId, namespace),
    onSuccess: (_data, variables) => {
      notify({ title: 'Signature Settings Deleted', message: 'APSignatures CR removed from cluster', severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafSignatures(clusterId, variables.namespace) });
    },
    onError: (error) => notifyError(error, 'deleting signature settings'),
  });
}

export function useRecompileWafPolicy(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<{ message: string }, Error, { name: string; namespace: string }>({
    mutationFn: ({ name, namespace }) => wafPoliciesApi.recompilePolicy(clusterId, name, namespace),
    onSuccess: (_data, variables) => {
      notify({ title: 'Recompile Triggered', message: `${variables.name} — compiler will rebuild the bundle`, severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafPolicies(clusterId) });
    },
    onError: (error) => notifyError(error, 'triggering recompile'),
  });
}

// ---------------------------------------------------------------------------
// APUserSig
// ---------------------------------------------------------------------------

export function useWafUserSigs(clusterId: number, namespace?: string, options?: { enabled?: boolean; autoRefresh?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.wafUserSigs(clusterId, namespace),
    queryFn: () => wafPoliciesApi.listUserSigs(clusterId, { namespace }),
    enabled: options?.enabled !== false && !!clusterId,
    refetchInterval: options?.autoRefresh !== false ? 10_000 : false,
  });
}

export function useCreateWafUserSig(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<unknown, Error, WafUserSigCreateRequest>({
    mutationFn: (data) => wafPoliciesApi.createUserSig(clusterId, data),
    onSuccess: () => {
      notify({ title: 'User Signature Created', severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafUserSigs(clusterId) });
    },
    onError: (error) => notifyError(error, 'creating user signature'),
  });
}

export function useUpdateWafUserSig(clusterId: number, name: string) {
  const queryClient = useQueryClient();
  return useAppMutation<APUserSigResource, Error, WafUserSigUpdateRequest>({
    mutationFn: (data) => wafPoliciesApi.updateUserSig(clusterId, name, data),
    onSuccess: () => {
      notify({ title: 'User Signature Updated', severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafUserSigs(clusterId) });
    },
    onError: (error) => notifyError(error, 'updating user signature'),
  });
}

export function useDeleteWafUserSig(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<{ message: string }, Error, { name: string; namespace: string }>({
    mutationFn: ({ name, namespace }) => wafPoliciesApi.deleteUserSig(clusterId, name, { namespace }),
    onSuccess: (_data, variables) => {
      notify({ title: 'User Signature Deleted', message: variables.name, severity: 'success' });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafUserSigs(clusterId) });
    },
    onError: (error) => notifyError(error, 'deleting user signature'),
  });
}
