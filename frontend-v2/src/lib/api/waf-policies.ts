/**
 * WAF Policy Manager API methods
 *
 * Calls the bnk-forge backend routes under /api/k8s/clusters/{id}/waf/*,
 * which in turn perform generic CRD CRUD against the target cluster's
 * appprotect.f5.com/v1 resources (APPolicy, APLogConf, APSignatures, APUserSig).
 * See docs/WAF_POLICY_MANAGER_DESIGN.md for the full design.
 */
import { apiClient } from './client';
import type {
  APPolicyResource,
  APLogConfResource,
  APSignaturesResource,
  APUserSigResource,
  WafPolicyCreateRequest,
  WafPolicyUpdateRequest,
  WafLogConfCreateRequest,
  WafLogConfUpdateRequest,
  WafUserSigCreateRequest,
  WafUserSigUpdateRequest,
  WafSignaturesUpdateRequest,
} from '@/types';

export const wafPoliciesApi = {
  // APPolicy
  listPolicies: (clusterId: number, params?: { namespace?: string }) =>
    apiClient
      .get<{ policies: APPolicyResource[]; count: number }>(`/api/k8s/clusters/${clusterId}/waf/policies`, { params })
      .then((res) => res.data),

  getPolicy: (clusterId: number, name: string, params: { namespace: string }) =>
    apiClient
      .get<APPolicyResource>(`/api/k8s/clusters/${clusterId}/waf/policies/${name}`, { params })
      .then((res) => res.data),

  createPolicy: (clusterId: number, data: WafPolicyCreateRequest) =>
    apiClient.post<APPolicyResource>(`/api/k8s/clusters/${clusterId}/waf/policies`, data).then((res) => res.data),

  updatePolicy: (clusterId: number, name: string, data: WafPolicyUpdateRequest) =>
    apiClient.put<APPolicyResource>(`/api/k8s/clusters/${clusterId}/waf/policies/${name}`, data).then((res) => res.data),

  deletePolicy: (clusterId: number, name: string, params: { namespace: string }) =>
    apiClient
      .delete<{ message: string }>(`/api/k8s/clusters/${clusterId}/waf/policies/${name}`, { params })
      .then((res) => res.data),

  // APLogConf
  listLogConfs: (clusterId: number, params?: { namespace?: string }) =>
    apiClient
      .get<{ log_confs: APLogConfResource[]; count: number }>(`/api/k8s/clusters/${clusterId}/waf/logconfs`, { params })
      .then((res) => res.data),

  createLogConf: (clusterId: number, data: WafLogConfCreateRequest) =>
    apiClient.post<APLogConfResource>(`/api/k8s/clusters/${clusterId}/waf/logconfs`, data).then((res) => res.data),

  updateLogConf: (clusterId: number, name: string, data: WafLogConfUpdateRequest) =>
    apiClient.put<APLogConfResource>(`/api/k8s/clusters/${clusterId}/waf/logconfs/${name}`, data).then((res) => res.data),

  deleteLogConf: (clusterId: number, name: string, params: { namespace: string }) =>
    apiClient
      .delete<{ message: string }>(`/api/k8s/clusters/${clusterId}/waf/logconfs/${name}`, { params })
      .then((res) => res.data),

  // APSignatures — singleton per namespace (metadata.name must be "apsignatures")
  getSignatures: (clusterId: number, params: { namespace: string }) =>
    apiClient
      .get<APSignaturesResource | null>(`/api/k8s/clusters/${clusterId}/waf/signatures`, { params })
      .then((res) => res.data),

  upsertSignatures: (clusterId: number, data: WafSignaturesUpdateRequest) =>
    apiClient.put<APSignaturesResource>(`/api/k8s/clusters/${clusterId}/waf/signatures`, data).then((res) => res.data),

  deleteSignatures: (clusterId: number, namespace: string) =>
    apiClient.delete<{ message: string }>(`/api/k8s/clusters/${clusterId}/waf/signatures`, { params: { namespace } }).then((res) => res.data),

  recompilePolicy: (clusterId: number, name: string, namespace: string) =>
    apiClient.post<{ message: string }>(`/api/k8s/clusters/${clusterId}/waf/policies/${name}/recompile`, null, { params: { namespace } }).then((res) => res.data),

  // APUserSig
  listUserSigs: (clusterId: number, params?: { namespace?: string }) =>
    apiClient
      .get<{ user_sigs: APUserSigResource[]; count: number }>(`/api/k8s/clusters/${clusterId}/waf/usersigs`, { params })
      .then((res) => res.data),

  createUserSig: (clusterId: number, data: WafUserSigCreateRequest) =>
    apiClient.post<APUserSigResource>(`/api/k8s/clusters/${clusterId}/waf/usersigs`, data).then((res) => res.data),

  updateUserSig: (clusterId: number, name: string, data: WafUserSigUpdateRequest) =>
    apiClient.put<APUserSigResource>(`/api/k8s/clusters/${clusterId}/waf/usersigs/${name}`, data).then((res) => res.data),

  deleteUserSig: (clusterId: number, name: string, params: { namespace: string }) =>
    apiClient
      .delete<{ message: string }>(`/api/k8s/clusters/${clusterId}/waf/usersigs/${name}`, { params })
      .then((res) => res.data),
};
