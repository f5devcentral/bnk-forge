// Alert Channel types

export type AlertChannelType = 'webhook' | 'slack' | 'msteams' | 'email';
export type AlertEventType = 'health_change' | 'drift_detected' | 'deploy_success' | 'deploy_failed' | 'deploy_started';

export interface AlertChannel {
  id: number;
  name: string;
  channel_type: AlertChannelType;
  enabled: boolean;
  url?: string;
  headers?: Record<string, string>;
  event_types: AlertEventType[];
  project_ids: number[];
  cluster_ids: number[];
  min_interval_seconds: number;
  description?: string;
  total_sent: number;
  total_failed: number;
  last_error?: string;
  last_fired_at?: string;
  last_success_at?: string;
  created_by?: string;
  created_at: string;
  updated_at: string;
  recent_history?: AlertHistoryEntry[];
}

export interface AlertChannelCreate {
  name: string;
  channel_type: AlertChannelType;
  url?: string;
  headers?: Record<string, string>;
  secret?: string;
  event_types?: AlertEventType[];
  project_ids?: number[];
  cluster_ids?: number[];
  min_interval_seconds?: number;
  description?: string;
  enabled?: boolean;
}

export interface AlertHistoryEntry {
  id: number;
  channel_id?: number;
  event_type: string;
  severity?: string;
  title: string;
  message?: string;
  status: 'sent' | 'failed' | 'rate_limited';
  http_status_code?: number;
  error_message?: string;
  duration_ms?: number;
  created_at: string;
}
