import { apiClient } from './client';

export type TimeRange = '1h' | '24h' | '7d' | '30d';

export interface DashboardUnavailable {
  available: false;
  reason: string;
}

export interface DashboardStatus {
  available: true;
  total_events_30d: number;
}

export interface DashboardSummary {
  available: true;
  time_range: TimeRange;
  total: number;
  rejected: number;
  alerted: number;
  passed: number;
  rejected_pct: number;
  unique_ips: number;
  top_attack_type: string;
  top_attack_count: number;
}

export interface TrendPoint {
  ts: string;
  REJECTED: number;
  PASSED: number;
  ALERTED: number;
}

export interface DashboardTrend {
  available: true;
  time_range: TimeRange;
  bucket_hours: number;
  series: TrendPoint[];
}

export interface AttackTypeItem {
  attack_type: string;
  count: number;
  rejected: number;
  pct: number;
}

export interface DashboardTopAttacks {
  available: true;
  time_range: TimeRange;
  items: AttackTypeItem[];
}

export interface IpItem {
  ip: string;
  total_hits: number;
  blocked_hits: number;
  last_seen: string;
}

export interface DashboardTopIps {
  available: true;
  time_range: TimeRange;
  items: IpItem[];
}

export interface UriItem {
  uri: string;
  count: number;
  rejected: number;
  attack_types: string[];
  last_seen: string;
}

export interface DashboardTopUris {
  available: true;
  time_range: TimeRange;
  items: UriItem[];
}

export interface PolicyItem {
  policy_name: string;
  hits: number;
  blocked: number;
  unique_uris: number;
  unique_ips: number;
}

export interface DashboardTopPolicies {
  available: true;
  time_range: TimeRange;
  items: PolicyItem[];
}

export interface MethodItem { method: string; count: number; }
export interface DashboardRequestMethods { available: true; time_range: TimeRange; items: MethodItem[]; }

export interface SeverityItem { rating: number; label: string; count: number; }
export interface DashboardSeverity { available: true; time_range: TimeRange; items: SeverityItem[]; }

export interface SignatureItem { sig_name: string; hits: number; unique_ips: number; unique_uris: number; blocked: number; }
export interface DashboardTopSignatures { available: true; time_range: TimeRange; items: SignatureItem[]; }

export interface InstanceItem { vs_name: string; hits: number; blocked: number; unique_uris: number; unique_ips: number; }
export interface DashboardTopInstances { available: true; time_range: TimeRange; items: InstanceItem[]; }

export interface DashboardSupportIdEvent {
  available: true;
  ts: string; outcome: string; attack_type: string; ip_client: string;
  method: string; uri: string; policy_name: string; vs_name: string;
  violation_rating: number; sig_ids: string; sig_names: string;
  support_id: string; namespace: string; ingest_source: string; raw_message: string;
}

const base = (clusterId: number) =>
  `/api/k8s/clusters/${clusterId}/waf/dashboard`;

export const wafDashboardApi = {
  getStatus: (clusterId: number) =>
    apiClient
      .get<DashboardStatus | DashboardUnavailable>(`${base(clusterId)}/status`)
      .then((r) => r.data),

  getSummary: (clusterId: number, timeRange: TimeRange) =>
    apiClient
      .get<DashboardSummary | DashboardUnavailable>(`${base(clusterId)}/summary`, {
        params: { time_range: timeRange },
      })
      .then((r) => r.data),

  getTrend: (clusterId: number, timeRange: TimeRange) =>
    apiClient
      .get<DashboardTrend | DashboardUnavailable>(`${base(clusterId)}/trend`, {
        params: { time_range: timeRange },
      })
      .then((r) => r.data),

  getTopAttacks: (clusterId: number, timeRange: TimeRange, limit = 10) =>
    apiClient
      .get<DashboardTopAttacks | DashboardUnavailable>(`${base(clusterId)}/top-attacks`, {
        params: { time_range: timeRange, limit },
      })
      .then((r) => r.data),

  getTopIps: (clusterId: number, timeRange: TimeRange, limit = 10) =>
    apiClient
      .get<DashboardTopIps | DashboardUnavailable>(`${base(clusterId)}/top-ips`, {
        params: { time_range: timeRange, limit },
      })
      .then((r) => r.data),

  getTopUris: (clusterId: number, timeRange: TimeRange, limit = 10) =>
    apiClient
      .get<DashboardTopUris | DashboardUnavailable>(`${base(clusterId)}/top-uris`, {
        params: { time_range: timeRange, limit },
      })
      .then((r) => r.data),

  getTopPolicies: (clusterId: number, timeRange: TimeRange, limit = 10) =>
    apiClient
      .get<DashboardTopPolicies | DashboardUnavailable>(`${base(clusterId)}/top-policies`, {
        params: { time_range: timeRange, limit },
      })
      .then((r) => r.data),

  getRequestMethods: (clusterId: number, timeRange: TimeRange) =>
    apiClient
      .get<DashboardRequestMethods | DashboardUnavailable>(`${base(clusterId)}/request-methods`, {
        params: { time_range: timeRange },
      })
      .then((r) => r.data),

  getSeverity: (clusterId: number, timeRange: TimeRange) =>
    apiClient
      .get<DashboardSeverity | DashboardUnavailable>(`${base(clusterId)}/severity`, {
        params: { time_range: timeRange },
      })
      .then((r) => r.data),

  getTopSignatures: (clusterId: number, timeRange: TimeRange, limit = 10) =>
    apiClient
      .get<DashboardTopSignatures | DashboardUnavailable>(`${base(clusterId)}/top-signatures`, {
        params: { time_range: timeRange, limit },
      })
      .then((r) => r.data),

  getTopInstances: (clusterId: number, timeRange: TimeRange, limit = 10) =>
    apiClient
      .get<DashboardTopInstances | DashboardUnavailable>(`${base(clusterId)}/top-instances`, {
        params: { time_range: timeRange, limit },
      })
      .then((r) => r.data),

  getSupportId: (clusterId: number, supportId: string) =>
    apiClient
      .get<DashboardSupportIdEvent | DashboardUnavailable>(`${base(clusterId)}/support-id`, {
        params: { support_id: supportId },
      })
      .then((r) => r.data),
};
