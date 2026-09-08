import { apiClient } from './client';

export interface NapLogEntry {
  raw: string;
  date_time?: string;
  unit_hostname?: string;
  policy_name?: string;
  vs_name?: string;
  outcome?: string;
  violation_rating?: string;
  attack_type?: string;
  violations?: string;
  sig_ids?: string;
  sig_names?: string;
  client_ip?: string;
  method?: string;
  request?: string;
  uri?: string;
  support_id?: string;
  request_status?: string;
  severity?: string;
  [key: string]: string | undefined;
}

export interface WafSecurityLogsResponse {
  entries: NapLogEntry[];
  total: number;
  source_endpoint: string | null;
  all_endpoints?: string[];
  cr_kind: string;
  cr_name: string;
  warning?: string;
  error?: string | null;
}

export interface WafLogsParams {
  namespace: string;
  cr_kind?: 'appolicy' | 'f5virtualserver';
  cr_name?: string;
  limit?: number;
  outcome_filter?: string;
  attack_type_filter?: string;
  vs_name_filter?: string;
  ip_filter?: string;
  uri_filter?: string;
}

export const wafLogsApi = {
  getSecurityLogs: (clusterId: number, params: WafLogsParams) =>
    apiClient
      .get<WafSecurityLogsResponse>(`/api/k8s/clusters/${clusterId}/waf/security-logs`, { params })
      .then((res) => res.data),
};
