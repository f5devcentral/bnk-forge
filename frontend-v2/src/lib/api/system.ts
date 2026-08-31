/**
 * System Administration API methods
 */
import { apiClient } from './client';
import type {
  SystemHealth,
  ProcessMetrics,
  QueueMetrics,
  PerformanceMetrics,
  ErrorsList,
  DatabaseStats,
  CleanupResult,
  VacuumResult,
  CleanupType,
  VersionInfo,
  UpgradeResponse,
  UpgradeState,
  UpgradeVerification,
  BnkConsumptionResponse,
} from '@/types/system';

export const systemApi = {
  // System Administration & Monitoring
  getSystemHealth: () =>
    apiClient.get<SystemHealth>('/api/system/health').then((res) => res.data),

  getProcessMetrics: () =>
    apiClient.get<ProcessMetrics>('/api/system/process-metrics').then((res) => res.data),

  getQueueMetrics: () =>
    apiClient.get<QueueMetrics>('/api/system/queue-metrics').then((res) => res.data),

  getPerformanceMetrics: () =>
    apiClient.get<PerformanceMetrics>('/api/system/performance').then((res) => res.data),

  getRecentErrors: (limit = 10) =>
    apiClient.get<ErrorsList>(`/api/system/errors?limit=${limit}`).then((res) => res.data),

  getDatabaseStats: () =>
    apiClient.get<DatabaseStats>('/api/system/database/stats').then((res) => res.data),

  cleanupDatabase: (type: CleanupType, olderThanDays: number) =>
    apiClient.post<CleanupResult>('/api/system/database/cleanup', {
      cleanup_type: type,
      older_than_days: olderThanDays,
    }).then((res) => res.data),

  vacuumDatabase: () =>
    apiClient.post<VacuumResult>('/api/system/database/vacuum').then((res) => res.data),

  // Container Management
  getContainerStatus: () =>
    apiClient.get<{ containers: Array<{ service: string; container_name: string; status: string; state: string }>; total: number }>('/api/system/containers/status').then((res) => res.data),

  restartContainers: (services?: string[]) =>
    apiClient.post<{ message: string; results: Array<{ service: string; status: string; container?: string; error?: string }>; note: string }>('/api/system/containers/restart', { services }).then((res) => res.data),

  // System Version & Upgrade
  getSystemVersion: () =>
    apiClient.get<VersionInfo>('/api/system/version').then((res) => res.data),

  triggerSystemUpgrade: () =>
    apiClient.post<UpgradeResponse>('/api/system/upgrade').then((res) => res.data),

  /** UP-011: Post-upgrade verification — checks all services, version, migrations */
  verifyPostUpgrade: () =>
    apiClient.get<UpgradeVerification>('/api/system/upgrade/verify').then((res) => res.data),

  /** UP-003: Get persisted upgrade state for recovery after page refresh */
  getUpgradeStatus: () =>
    apiClient.get<UpgradeState>('/api/system/upgrade/status').then((res) => res.data),

  // BNK Resource Consumption Dashboard
  getBnkConsumption: () =>
    apiClient.get<BnkConsumptionResponse>('/api/system/bnk-consumption').then((res) => res.data),

  // MCP Server
  getMCPStatus: () =>
    apiClient.get<MCPStatusResponse>('/api/system/mcp/status').then((res) => res.data),
};

// MCP types (co-located since they're only used here + the page)
export interface MCPTool {
  name: string;
  description: string;
}

export interface MCPToolCategory {
  category: string;
  icon: string;
  tools: MCPTool[];
}

export interface MCPStatusResponse {
  status: 'healthy' | 'degraded' | 'offline';
  latency_ms: number | null;
  error: string | null;
  endpoint: string;
  port: number;
  transport: string;
  total_tools: number;
  tool_catalog: MCPToolCategory[];
}
