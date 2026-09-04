export interface IngressSearchResult {
  kind: 'Ingress' | 'HTTPRoute' | 'VirtualServer' | 'Service' | string;
  name: string;
  namespace: string;
  matched_host: string;
  all_hosts: string[];
  cluster_id: number;
  cluster_name: string;
  cloud_provider?: string;
  region?: string;
  target_service?: string;
  status: string;
}

export interface ClusterSearchResult {
  id: number;
  name: string;
  cloud_provider?: string;
  region?: string;
  status: string;
  node_count?: number;
  detected_platform_profile?: string;
}

export interface ProjectSearchResult {
  id: number;
  name: string;
  description?: string;
  cloud_provider?: string;
  region?: string;
  module_count: number;
  deployed_count: number;
  failed_count: number;
}

export interface GlobalSearchResponse {
  query: string;
  ingresses: IngressSearchResult[];
  clusters: ClusterSearchResult[];
  projects: ProjectSearchResult[];
}
