import { apiClient } from './client';

export interface WafDashboardTab {
  id: number;
  cluster_id: number;
  name: string;
  tab_order: number;
}

const base = (clusterId: number) => `/api/k8s/clusters/${clusterId}/waf/dashboard-tabs`;

export const wafDashboardTabsApi = {
  list: (clusterId: number) =>
    apiClient.get<{ tabs: WafDashboardTab[] }>(base(clusterId)).then((r) => r.data.tabs),

  create: (clusterId: number, name: string) =>
    apiClient.post<WafDashboardTab>(base(clusterId), { name }).then((r) => r.data),

  rename: (clusterId: number, tabId: number, name: string) =>
    apiClient.patch<WafDashboardTab>(`${base(clusterId)}/${tabId}`, { name }).then((r) => r.data),

  remove: (clusterId: number, tabId: number) =>
    apiClient.delete(`${base(clusterId)}/${tabId}`).then((r) => r.data),
};
