import { apiClient } from './client';

export type ChartType = 'bar' | 'horizontal_bar' | 'area' | 'line' | 'pie' | 'kpi' | 'table';
export type TimeRange = '1h' | '24h' | '7d' | '30d';
export type PanelWidth = 'full' | 'half';

export interface WafPanelTemplate {
  key: string;
  description: string;
}

export interface WafPanel {
  id: number;
  cluster_id: number;
  tab_id: number | null;
  title: string;
  chart_type: ChartType;
  query_template: string;
  time_range: TimeRange;
  width: PanelWidth;
  panel_order: number;
  extra_config: Record<string, unknown> | null;
  created_at: string | null;
}

export interface PanelDataRow {
  label?: string;
  value?: number;
  ts_bucket?: string;
  REJECTED?: number;
  PASSED?: number;
  ALERTED?: number;
  [key: string]: string | number | undefined;
}

export interface PanelDataResponse {
  available: boolean;
  panel_id?: number;
  chart_type?: ChartType;
  title?: string;
  time_range?: TimeRange;
  rows?: PanelDataRow[];
  reason?: string;
}

export interface PanelCreatePayload {
  title: string;
  chart_type: ChartType;
  query_template: string;
  time_range: TimeRange;
  width: PanelWidth;
  panel_order: number;
  tab_id?: number; // 0 = default/legacy "Custom" tab (stored as NULL server-side)
}

const base = (clusterId: number) => `/api/k8s/clusters/${clusterId}/waf/panels`;

export const wafPanelsApi = {
  getTemplates: (clusterId: number) =>
    apiClient.get<{ templates: WafPanelTemplate[]; chart_types: string[]; time_ranges: string[] }>(
      `${base(clusterId)}/templates`,
    ).then((r) => r.data),

  list: (clusterId: number, tabId?: number) =>
    apiClient.get<{ panels: WafPanel[] }>(base(clusterId), {
      params: tabId !== undefined ? { tab_id: tabId } : undefined,
    }).then((r) => r.data.panels),

  create: (clusterId: number, payload: PanelCreatePayload) =>
    apiClient.post<WafPanel>(base(clusterId), payload).then((r) => r.data),

  update: (clusterId: number, panelId: number, payload: Partial<PanelCreatePayload>) =>
    apiClient.put<WafPanel>(`${base(clusterId)}/${panelId}`, payload).then((r) => r.data),

  remove: (clusterId: number, panelId: number) =>
    apiClient.delete(`${base(clusterId)}/${panelId}`).then((r) => r.data),

  getData: (clusterId: number, panelId: number, timeRange?: TimeRange) =>
    apiClient.get<PanelDataResponse>(`${base(clusterId)}/${panelId}/data`, {
      params: timeRange ? { time_range: timeRange } : undefined,
    }).then((r) => r.data),
};
