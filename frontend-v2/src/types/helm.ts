// Helm Release, Chart & Repository types

import type { JsonValue } from './common';

export interface HelmRelease {
  name: string;
  namespace: string;
  revision: string;
  updated: string;
  status: string;
  chart: string;
  app_version: string;
}

export interface HelmReleaseDetail {
  name: string;
  info: {
    first_deployed: string;
    last_deployed: string;
    deleted: string;
    description: string;
    status: string;
    notes: string;
  };
  chart: {
    metadata: {
      name: string;
      version: string;
      description: string;
      app_version: string;
    };
  };
  config: Record<string, JsonValue>;
  manifest: string;
  version: number;
  namespace: string;
}

export interface HelmRevision {
  revision: number;
  updated: string;
  status: string;
  chart: string;
  app_version: string;
  description: string;
}

export interface HelmChart {
  name: string;
  version: string;
  app_version: string;
  description: string;
  icon?: string;
  home?: string;
  sources?: string[];
  keywords?: string[];
  maintainers?: Array<{
    name: string;
    email?: string;
  }>;
}

export interface HelmRepository {
  name: string;
  url: string;
}

export interface InstallChartRequest {
  release_name: string;
  chart: string;
  namespace?: string;
  values?: Record<string, JsonValue>;
  version?: string;
  create_namespace?: boolean;
  wait?: boolean;
  timeout?: string;
}

export interface UpgradeReleaseRequest {
  chart?: string;
  values?: Record<string, JsonValue>;
  version?: string;
  // Note: reuse_values removed — not in backend UpgradeReleaseRequest schema
  install?: boolean;
  wait?: boolean;
  timeout?: string;
}

export interface RollbackReleaseRequest {
  revision?: number;
  wait?: boolean;
  timeout?: string;
}

export interface HelmReleasesResponse {
  success: boolean;
  releases: HelmRelease[];
  count: number;
}

export interface HelmReleaseResponse {
  success: boolean;
  release: HelmReleaseDetail;
}

export interface HelmHistoryResponse {
  success: boolean;
  history: HelmRevision[];
  count: number;
}

export interface HelmValuesResponse {
  success: boolean;
  values: Record<string, JsonValue>;
}

export interface HelmChartsResponse {
  success: boolean;
  charts: HelmChart[];
  count: number;
}

export interface HelmRepositoriesResponse {
  success: boolean;
  repositories: HelmRepository[];
  count: number;
}

export interface UploadedHelmChart {
  id: number;
  name: string;
  version: string;
  app_version?: string;
  description?: string;
  file_size: number;
  source_type?: 'uploaded' | 'cloned';
  source_chart?: string;
  source_repository?: string;
  uploaded_by?: string;
  created_at: string;
}

export interface UploadedChartsResponse {
  success: boolean;
  charts: UploadedHelmChart[];
  count: number;
}

export interface HelmRevisionComparison {
  success: boolean;
  revision1: {
    number: number;
    values: Record<string, JsonValue>;
    manifest: string;
  };
  revision2: {
    number: number;
    values: Record<string, JsonValue>;
    manifest: string;
  };
}
