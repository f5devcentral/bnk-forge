/**
 * Centralized Test Fixtures & Factories
 *
 * All mock data lives here. MSW handlers import from here (single source of truth).
 * Individual tests can import fixtures directly or use factory functions for customization.
 *
 * Naming conventions:
 *   - `mock*`       — static fixture objects (e.g., mockUser, mockProjects)
 *   - `create*`     — factory functions that accept partial overrides (e.g., createProject({name: 'custom'}))
 *   - `*Defaults`   — default field values used by factories
 */

// ============================================================================
// Auth / Users
// ============================================================================

export const mockUser = {
  id: 1,
  username: 'admin',
  email: 'admin@example.com',
  role: 'admin' as const,
  is_active: true,
  must_change_password: false,
  last_login_at: '2026-02-18T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
};

export const mockViewerUser = {
  id: 2,
  username: 'viewer',
  email: 'viewer@example.com',
  role: 'viewer' as const,
  is_active: true,
  must_change_password: false,
  last_login_at: '2026-02-17T00:00:00Z',
  created_at: '2026-01-05T00:00:00Z',
};

export const mockOperatorUser = {
  id: 3,
  username: 'operator',
  email: 'operator@example.com',
  role: 'operator' as const,
  is_active: true,
  must_change_password: false,
  last_login_at: '2026-02-16T00:00:00Z',
  created_at: '2026-01-10T00:00:00Z',
};

export const mockUsers = [mockUser, mockViewerUser, mockOperatorUser];

export function createUser(overrides: Partial<typeof mockUser> = {}) {
  return { ...mockUser, id: Math.floor(Math.random() * 10000), ...overrides };
}

// ============================================================================
// Projects
// ============================================================================

export const mockProjects = [
  {
    id: 1,
    name: 'test-project',
    description: 'A test project',
    project_type: 'cloud-aws' as const,
    target_platform_profile: 'eks' as const,
    platform_provider: 'aws' as const,
    management_boundary: 'forge_managed_components_only' as const,
    environment: 'dev',
    color: '#2563eb',
    icon: '',
    is_active: true,
    has_deployments: false,
    module_count: 2,
    deployed_count: 1,
    failed_count: 0,
    cluster_count: 1,
    region: 'us-east-1',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-15T00:00:00Z',
  },
  {
    id: 2,
    name: 'production-infra',
    description: 'Production infrastructure',
    project_type: 'kubernetes' as const,
    target_platform_profile: 'generic_onprem' as const,
    platform_provider: 'baremetal' as const,
    management_boundary: 'customer_managed_cluster' as const,
    environment: 'prod',
    color: '#dc2626',
    icon: '',
    is_active: true,
    has_deployments: true,
    module_count: 5,
    deployed_count: 5,
    failed_count: 0,
    cluster_count: 2,
    region: '',
    created_at: '2026-01-10T00:00:00Z',
    updated_at: '2026-02-01T00:00:00Z',
  },
  {
    id: 3,
    name: 'ibm-roks-prod',
    description: 'IBM Cloud ROKS production platform',
    project_type: 'cloud-ibm' as const,
    target_platform_profile: 'roks' as const,
    platform_provider: 'ibm' as const,
    management_boundary: 'forge_managed_components_only' as const,
    environment: 'prod',
    color: '#0ea5e9',
    icon: '',
    is_active: false,
    has_deployments: true,
    module_count: 3,
    deployed_count: 3,
    failed_count: 0,
    cluster_count: 1,
    region: 'us-south',
    credential_template_id: 7,
    credential_template: {
      id: 7,
      name: 'IBM Cloud',
      provider: 'ibm',
      has_ibmcloud_api_key: true,
      ibmcloud_resource_group: 'platform-rg',
    },
    created_at: '2026-02-10T00:00:00Z',
    updated_at: '2026-02-15T00:00:00Z',
  },
];

export function createProject(overrides: Partial<(typeof mockProjects)[0]> = {}) {
  return {
    ...mockProjects[0],
    id: Math.floor(Math.random() * 10000),
    name: `project-${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// Modules
// ============================================================================

export const mockModules = [
  {
    id: 1,
    name: 'vpc',
    project_id: 1,
    module_library_id: 1,
    status: 'applied',
    path_in_project: 'modules/vpc',
    deployment_order: 0,
    enabled: true,
    variables: { cidr_block: '10.0.0.0/16' },
    outputs: { vpc_id: 'vpc-12345' },
    library_module: { name: 'vpc', category: 'network', provider: 'aws' },
    created_at: '2026-01-15T00:00:00Z',
  },
  {
    id: 2,
    name: 'eks',
    project_id: 1,
    module_library_id: 2,
    status: 'not_initialized',
    path_in_project: 'modules/eks',
    deployment_order: 1,
    enabled: true,
    variables: { cluster_name: 'test-cluster' },
    outputs: null,
    library_module: { name: 'eks', category: 'kubernetes', provider: 'aws' },
    created_at: '2026-01-15T00:00:00Z',
  },
];

export const mockModuleLibrary = [
  {
    id: 1,
    name: 'vpc',
    category: 'network',
    provider: 'aws',
    description: 'AWS VPC module',
    is_official: true,
    is_active: true,
  },
  {
    id: 2,
    name: 'eks',
    category: 'kubernetes',
    provider: 'aws',
    description: 'AWS EKS cluster module',
    is_official: true,
    is_active: true,
  },
  {
    id: 3,
    name: 'bnk-controller',
    category: 'network',
    provider: 'f5',
    description: 'F5 BNK Controller',
    is_official: true,
    is_active: true,
  },
];

export function createModule(overrides: Partial<(typeof mockModules)[0]> = {}) {
  return {
    ...mockModules[0],
    id: Math.floor(Math.random() * 10000),
    name: `module-${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// Tasks
// ============================================================================

export const mockTasksList = [
  {
    id: 1,
    celery_task_id: 'abc-123',
    task_type: 'apply',
    status: 'completed',
    project_id: 1,
    project_name: 'test-project',
    module_id: 1,
    module_name: 'vpc',
    created_at: '2026-02-18T10:00:00Z',
    started_at: '2026-02-18T10:00:01Z',
    completed_at: '2026-02-18T10:05:00Z',
    duration_seconds: 299,
    logs: 'Apply complete! Resources: 3 added, 0 changed, 0 destroyed.',
  },
  {
    id: 2,
    celery_task_id: 'def-456',
    task_type: 'plan',
    status: 'in_progress',
    project_id: 1,
    project_name: 'test-project',
    module_id: 2,
    module_name: 'eks',
    created_at: '2026-02-18T11:00:00Z',
    started_at: '2026-02-18T11:00:01Z',
  },
];

export const mockTasks = {
  tasks: mockTasksList,
  total: mockTasksList.length,
  limit: 50,
  offset: 0,
};

export const mockTaskStats = {
  total: 42,
  completed: 35,
  failed: 3,
  in_progress: 2,
  pending: 2,
  avg_duration_seconds: 180,
  success_rate: 0.92,
};

export function createTask(overrides: Partial<(typeof mockTasksList)[0]> = {}) {
  return {
    ...mockTasksList[0],
    id: Math.floor(Math.random() * 10000),
    celery_task_id: `task-${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// K8s Clusters
// ============================================================================

export const mockClusters = {
  clusters: [
    {
      id: 1,
      name: 'dev-cluster',
      project_id: 1,
      cluster_type: 'eks',
      status: 'connected',
      detected_platform_profile: 'eks',
      detected_platform_provider: 'aws',
      platform_capabilities: { cloud_load_balancer: true },
      platform_constraints: { managed_control_plane: true },
      api_server: 'https://dev.eks.amazonaws.com',
      version: '1.28',
      node_count: 3,
      created_at: '2026-01-15T00:00:00Z',
      updated_at: '2026-02-01T00:00:00Z',
    },
    {
      id: 2,
      name: 'prod-cluster',
      project_id: 2,
      cluster_type: 'eks',
      status: 'connected',
      detected_platform_profile: 'generic_onprem',
      detected_platform_provider: 'baremetal',
      platform_capabilities: { network_attachment_definitions: true },
      platform_constraints: { cluster_lifecycle_external: true },
      api_server: 'https://prod.eks.amazonaws.com',
      version: '1.29',
      node_count: 6,
      created_at: '2026-01-20T00:00:00Z',
      updated_at: '2026-02-15T00:00:00Z',
    },
  ],
  count: 2,
};

export function createCluster(overrides: Partial<(typeof mockClusters.clusters)[0]> = {}) {
  return {
    ...mockClusters.clusters[0],
    id: Math.floor(Math.random() * 10000),
    name: `cluster-${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// K8s Resources
// ============================================================================

export const mockK8sResources = {
  resources: [
    {
      name: 'nginx-deployment',
      namespace: 'default',
      kind: 'Deployment',
      status: 'Running',
      replicas: '3/3',
      age: '7d',
      labels: { app: 'nginx' },
    },
    {
      name: 'redis-pod',
      namespace: 'cache',
      kind: 'Pod',
      status: 'Running',
      age: '2d',
      labels: { app: 'redis' },
    },
  ],
  total: 2,
};

// ============================================================================
// Helm
// ============================================================================

export const mockHelmReleases = [
  {
    name: 'nginx-ingress',
    namespace: 'ingress-nginx',
    revision: '3',
    status: 'deployed',
    chart: 'ingress-nginx-4.9.0',
    app_version: '1.9.5',
    updated: '2026-02-01T12:00:00Z',
  },
  {
    name: 'cert-manager',
    namespace: 'cert-manager',
    revision: '1',
    status: 'deployed',
    chart: 'cert-manager-1.14.0',
    app_version: '1.14.0',
    updated: '2026-01-20T10:00:00Z',
  },
];

export const mockHelmRepositories = [
  {
    name: 'bitnami',
    url: 'https://charts.bitnami.com/bitnami',
    status: 'ready',
    last_updated: '2026-02-15T00:00:00Z',
  },
  {
    name: 'ingress-nginx',
    url: 'https://kubernetes.github.io/ingress-nginx',
    status: 'ready',
    last_updated: '2026-02-10T00:00:00Z',
  },
];

export function createHelmRelease(overrides: Partial<(typeof mockHelmReleases)[0]> = {}) {
  return {
    ...mockHelmReleases[0],
    name: `release-${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// Stacks
// ============================================================================

export const mockStackTemplates = [
  {
    id: 1,
    name: 'BNK Full Stack',
    slug: 'bnk-full-stack',
    description: 'Complete BNK deployment with networking and security',
    category: 'network',
    cloud_provider: 'aws',
    is_public: true,
    is_active: true,
    modules: ['vpc', 'eks', 'bnk-controller', 'bnk-tmm'],
    estimated_time: '30 min',
    estimated_cost: '$450/mo',
  },
  {
    id: 2,
    name: 'Minimal K8s',
    slug: 'minimal-k8s',
    description: 'Simple EKS cluster with VPC',
    category: 'kubernetes',
    cloud_provider: 'aws',
    is_public: true,
    is_active: true,
    modules: ['vpc', 'eks'],
    estimated_time: '15 min',
    estimated_cost: '$150/mo',
  },
];

export const mockStackInstances = [
  {
    id: 1,
    project_id: 1,
    template_id: 1,
    template_name: 'BNK Full Stack',
    status: 'deployed',
    created_at: '2026-02-01T00:00:00Z',
    deployed_at: '2026-02-01T01:00:00Z',
    modules: [1, 2],
  },
];

export function createStackTemplate(overrides: Partial<(typeof mockStackTemplates)[0]> = {}) {
  return {
    ...mockStackTemplates[0],
    id: Math.floor(Math.random() * 10000),
    slug: `stack-${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// Secrets
// ============================================================================

export const mockSecrets = [
  {
    id: 1,
    name: 'AWS_ACCESS_KEY_ID',
    project_id: 1,
    is_file: false,
    created_at: '2026-01-15T00:00:00Z',
    updated_at: '2026-01-15T00:00:00Z',
  },
  {
    id: 2,
    name: 'kubeconfig.yaml',
    project_id: 1,
    is_file: true,
    created_at: '2026-01-20T00:00:00Z',
    updated_at: '2026-01-20T00:00:00Z',
  },
];

export function createSecret(overrides: Partial<(typeof mockSecrets)[0]> = {}) {
  return {
    ...mockSecrets[0],
    id: Math.floor(Math.random() * 10000),
    name: `SECRET_${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// Operators
// ============================================================================

export const mockOperators = [
  {
    id: 'op-1',
    name: 'prod-operator',
    status: 'connected',
    last_seen: '2026-02-18T10:00:00Z',
    version: '2.10.45',
    cluster_name: 'prod-cluster',
    capabilities: ['deploy', 'health', 'config-export'],
  },
  {
    id: 'op-2',
    name: 'dev-operator',
    status: 'disconnected',
    last_seen: '2026-02-17T08:00:00Z',
    version: '2.10.40',
    cluster_name: 'dev-cluster',
    capabilities: ['deploy', 'health'],
  },
];

export const mockConnectivityModes = [
  {
    mode: 'websocket',
    name: 'WebSocket',
    description: 'Real-time bidirectional communication',
    recommended: true,
  },
  {
    mode: 'polling',
    name: 'HTTP Polling',
    description: 'Periodic HTTP polling for restricted networks',
    recommended: false,
  },
];

export function createOperator(overrides: Partial<(typeof mockOperators)[0]> = {}) {
  return {
    ...mockOperators[0],
    id: `op-${Date.now()}`,
    name: `operator-${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// Credential Templates
// ============================================================================

export const mockCredentialTemplates = [
  {
    id: 1,
    name: 'AWS Production',
    provider: 'aws',
    auth_type: 'access_key',
    description: 'Production AWS credentials',
    has_aws_secret_access_key: true,
    has_aws_session_token: false,
    aws_sso_enabled: false,
    has_gcp_credentials: false,
    has_azure_credentials: false,
    has_ibmcloud_api_key: false,
    projects_count: 0,
    is_active: true,
    created_at: '2026-01-10T00:00:00Z',
    updated_at: '2026-02-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'IBM Production',
    provider: 'ibm',
    description: 'Production IBM Cloud credentials',
    region: 'us-south',
    ibmcloud_resource_group: 'default',
    has_ibmcloud_api_key: true,
    ibm_cos_instance_name: 'bnk-orchestration',
    has_aws_secret_access_key: false,
    has_aws_session_token: false,
    aws_sso_enabled: false,
    has_gcp_credentials: false,
    has_azure_credentials: false,
    projects_count: 1,
    is_active: true,
    created_at: '2026-01-15T00:00:00Z',
    updated_at: '2026-02-10T00:00:00Z',
  },
  {
    id: 3,
    name: 'GCP Production',
    provider: 'gcp',
    auth_type: 'service_account',
    description: 'Production GCP credentials',
    has_aws_secret_access_key: false,
    has_aws_session_token: false,
    aws_sso_enabled: false,
    has_gcp_credentials: true,
    has_azure_credentials: false,
    has_ibmcloud_api_key: false,
    projects_count: 0,
    is_active: true,
    created_at: '2026-01-10T00:00:00Z',
    updated_at: '2026-02-01T00:00:00Z',
  },
  {
    id: 4,
    name: 'Azure Production',
    provider: 'azure',
    auth_type: 'service_principal',
    description: 'Production Azure credentials',
    has_aws_secret_access_key: false,
    has_aws_session_token: false,
    aws_sso_enabled: false,
    has_gcp_credentials: false,
    has_azure_credentials: true,
    has_ibmcloud_api_key: false,
    projects_count: 0,
    is_active: true,
    created_at: '2026-01-10T00:00:00Z',
    updated_at: '2026-02-01T00:00:00Z',
  },
];

export function createCredentialTemplate(overrides: Partial<(typeof mockCredentialTemplates)[0]> = {}) {
  return {
    ...mockCredentialTemplates[0],
    id: Math.floor(Math.random() * 10000),
    name: `cred-${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// Drift Detection
// ============================================================================

export const mockDriftSummary = {
  total_checks: 5,
  drifted: 1,
  clean: 4,
  drifted_modules: [
    {
      project_id: 1,
      project_name: 'test-project',
      module_id: 1,
      module_name: 'vpc',
      drift_type: 'modified',
      resources_added: 0,
      resources_modified: 1,
      resources_removed: 0,
      last_checked: '2026-02-18T09:00:00Z',
    },
  ],
};

export const mockDriftSettings = {
  enabled: true,
  interval_hours: 6,
  notify_on_drift: true,
  auto_remediate: false,
};

export const mockDriftChecks = [
  {
    id: 1,
    project_id: 1,
    module_id: 1,
    status: 'drifted',
    drift_type: 'modified',
    resources_added: 0,
    resources_modified: 1,
    resources_removed: 0,
    checked_at: '2026-02-18T09:00:00Z',
    details: { changed_resources: ['aws_security_group.main'] },
  },
];

export const mockDriftStats = {
  total_checks: 42,
  drifted_count: 5,
  clean_count: 37,
  last_check_at: '2026-02-18T09:00:00Z',
  avg_check_duration_seconds: 12,
};

// ============================================================================
// System / Health
// ============================================================================

export const mockSystemHealth = {
  status: 'healthy',
  version: '2.10.49',
  uptime_seconds: 86400,
  database: { status: 'healthy', latency_ms: 2 },
  redis: { status: 'healthy', latency_ms: 1 },
  celery: { status: 'healthy', workers: 2 },
};

export const mockSystemDefaults = {
  project: { default_type: { value: 'cloud-aws' } },
};

export const mockQueueMetrics = {
  queues: [
    { name: 'default', pending: 3, active: 1, reserved: 0 },
    { name: 'k8s', pending: 0, active: 0, reserved: 0 },
  ],
  total_pending: 3,
  total_active: 1,
  workers: 2,
};

export const mockPerformanceMetrics = {
  cpu_percent: 23.5,
  memory_percent: 45.2,
  memory_used_mb: 512,
  memory_total_mb: 1024,
  disk_percent: 60.0,
  uptime_seconds: 86400,
  request_rate_per_minute: 120,
  avg_response_time_ms: 45,
};

export const mockRecentErrors = {
  errors: [
    {
      task_id: 101,
      type: 'connection_error',
      error: 'Connection refused to K8s API',
      timestamp: '2026-02-18T10:00:00Z',
      project: 'test-project',
      module: 'bnk-vpc',
      exit_code: 1,
    },
  ],
  total: 1,
};

export const mockDatabaseStats = {
  size_mb: 128,
  table_count: 14,
  total_rows: 15000,
  tables: [
    { name: 'tasks', rows: 5000, size_kb: 2048 },
    { name: 'projects', rows: 10, size_kb: 32 },
    { name: 'project_modules', rows: 25, size_kb: 64 },
  ],
};

export const mockContainerStatus = {
  containers: [
    { service: 'backend', container_name: 'bnk-forge-backend', status: 'running', state: 'healthy' },
    { service: 'frontend', container_name: 'bnk-forge-frontend', status: 'running', state: 'healthy' },
    { service: 'postgres', container_name: 'bnk-forge-postgres', status: 'running', state: 'healthy' },
    { service: 'redis', container_name: 'bnk-forge-redis', status: 'running', state: 'healthy' },
    { service: 'celery-worker', container_name: 'bnk-forge-celery-worker', status: 'running', state: 'healthy' },
    { service: 'proxy', container_name: 'bnk-forge-proxy', status: 'running', state: 'healthy' },
  ],
  total: 6,
};

export const mockSystemVersion = {
  current_version: '2.10.49',
  latest_version: '2.10.50',
  update_available: true,
  commits_behind: 3,
  upgrade_readiness: {
    host_repo_path_set: true,
    docker_socket_available: true,
    upgrade_ready: true,
    upgrade_in_progress: false,
  },
};

export const mockBackupStatus = {
  in_progress: false,
  operation: null,
  started_at: null,
  message: null,
};

export const mockBackupStatusInProgress = {
  in_progress: true,
  operation: 'restore' as const,
  started_at: '2026-04-13T12:00:00Z',
  message: 'Database restore in progress. Please wait...',
};

export const mockMaintenanceStatus = {
  maintenance_mode: false,
  message: null,
  started_at: null,
};

export const mockMaintenanceActive = {
  maintenance_mode: true,
  message: 'System maintenance in progress',
  started_at: '2026-04-13T12:00:00Z',
};

// ============================================================================
// Alert Channels
// ============================================================================

export const mockAlertChannels = [
  {
    id: 1,
    name: 'Slack #ops',
    channel_type: 'slack',
    enabled: true,
    config: { webhook_url: 'https://hooks.slack.com/services/xxx' },
    created_at: '2026-01-15T00:00:00Z',
    updated_at: '2026-02-01T00:00:00Z',
    last_triggered_at: '2026-02-18T08:00:00Z',
  },
  {
    id: 2,
    name: 'PagerDuty Critical',
    channel_type: 'pagerduty',
    enabled: true,
    config: { integration_key: 'pd-xxx' },
    created_at: '2026-01-20T00:00:00Z',
    updated_at: '2026-02-05T00:00:00Z',
    last_triggered_at: null,
  },
];

export const mockAlertHistory = [
  {
    id: 1,
    channel_id: 1,
    channel_name: 'Slack #ops',
    event_type: 'drift_detected',
    severity: 'warning',
    message: 'Drift detected in vpc module',
    sent_at: '2026-02-18T08:00:00Z',
    success: true,
  },
];

export function createAlertChannel(overrides: Partial<(typeof mockAlertChannels)[0]> = {}) {
  return {
    ...mockAlertChannels[0],
    id: Math.floor(Math.random() * 10000),
    name: `alert-${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// Snapshots
// ============================================================================

export const mockSnapshots = {
  snapshots: [
    {
      id: 1,
      project_id: 1,
      name: 'Pre-upgrade snapshot',
      description: 'Snapshot before upgrading BNK controller',
      snapshot_type: 'manual',
      created_at: '2026-02-15T10:00:00Z',
      created_by: 'admin',
      size_bytes: 4096,
    },
    {
      id: 2,
      project_id: 1,
      name: 'Auto snapshot',
      description: 'Automatic snapshot before deploy',
      snapshot_type: 'auto',
      created_at: '2026-02-18T08:00:00Z',
      created_by: 'system',
      size_bytes: 3072,
    },
  ],
  total: 2,
};

export const mockSnapshotDetail = {
  ...mockSnapshots.snapshots[0],
  config_data: {
    modules: [
      { name: 'vpc', variables: { cidr_block: '10.0.0.0/16' } },
      { name: 'eks', variables: { cluster_name: 'prod' } },
    ],
    secrets: ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
  },
};

export function createSnapshot(overrides: Partial<(typeof mockSnapshots.snapshots)[0]> = {}) {
  return {
    ...mockSnapshots.snapshots[0],
    id: Math.floor(Math.random() * 10000),
    name: `snapshot-${Date.now()}`,
    ...overrides,
  };
}

// ============================================================================
// Fleet
// ============================================================================

export const mockFleetHealth = {
  total_clusters: 2,
  healthy: 1,
  warning: 1,
  critical: 0,
  offline: 0,
  operators: [
    {
      operator_id: 1,
      operator_uuid: 'op-uuid-1',
      cluster_id: 1,
      cluster_name: 'prod-cluster',
      status: 'healthy',
      last_seen: '2026-02-18T10:00:00Z',
      bnk_version: '2.3.0',
      route_count: 5,
      tmm_count: 2,
      gateway_count: 1,
      uptime: '24h',
      health_summary: { healthy: 3, warning: 0, critical: 0 },
      kubernetes_version: '1.28.4',
      node_count: 3,
      operator_version: null,
      connectivity_mode: 'kubeconfig',
    },
    {
      operator_id: 2,
      operator_uuid: 'op-uuid-2',
      cluster_id: 2,
      cluster_name: 'dev-cluster',
      status: 'warning',
      last_seen: '2026-02-17T08:00:00Z',
      bnk_version: '2.2.0',
      route_count: 2,
      tmm_count: 1,
      gateway_count: 1,
      uptime: '2h',
      health_summary: { healthy: 1, warning: 1, critical: 0 },
      kubernetes_version: '1.27.8',
      node_count: 2,
      operator_version: null,
      connectivity_mode: 'kubeconfig',
    },
  ],
};

export const mockFleetTargets = [
  {
    id: 1,
    project_id: null,
    name: 'prod-fleet',
    description: 'Production fleet',
    selector: { labels: ['env=prod'] },
    pinned_member_ids: null,
    created_by: 'admin',
    created_at: '2026-01-01T00:00:00Z',
  },
];

export const mockFleetCompareResult = {
  operator_a: 'prod-cluster',
  operator_b: 'dev-cluster',
  comparison_mode: 'health_report',
  total_diffs: 1,
  summary: '1 difference(s) between prod-cluster and dev-cluster',
  differences: [
    {
      field: 'Route Count',
      key: 'routes_count',
      value_a: 5,
      value_b: 2,
    },
  ],
};

// ============================================================================
// Config Promotion
// ============================================================================

export const mockPromoteResponse = {
  success: true,
  message: 'Config promoted successfully',
  changes_applied: 3,
  source_cluster: 'prod-cluster',
  target_cluster: 'staging-cluster',
};

export const mockPromotionHistory = {
  promotions: [
    {
      id: 1,
      source_cluster_id: 1,
      source_cluster_name: 'prod-cluster',
      target_cluster_id: 2,
      target_cluster_name: 'staging-cluster',
      changes_applied: 3,
      promoted_by: 'admin',
      promoted_at: '2026-02-18T10:00:00Z',
      dry_run: false,
    },
  ],
  total: 1,
};

// ============================================================================
// Runbooks
// ============================================================================

export const mockRunbooks = {
  runbooks: [
    {
      id: 'health-check',
      name: 'Cluster Health Check',
      description: 'Run comprehensive health checks on a Kubernetes cluster',
      steps: [
        { name: 'Check node status', command: 'get_health' },
        { name: 'Check pod status', command: 'get_resources' },
        { name: 'Scan cluster', command: 'scan_cluster' },
      ],
      estimated_duration: '2 min',
      category: 'diagnostics',
    },
    {
      id: 'rolling-restart',
      name: 'Rolling Restart',
      description: 'Perform a rolling restart of all deployments',
      steps: [
        { name: 'List deployments', command: 'get_resources' },
        { name: 'Restart each', command: 'restart_deployment' },
      ],
      estimated_duration: '5 min',
      category: 'operations',
    },
  ],
  total: 2,
};

export const mockRunbookResult = {
  runbook_id: 'health-check',
  status: 'completed',
  started_at: '2026-02-18T10:00:00Z',
  completed_at: '2026-02-18T10:02:00Z',
  steps: [
    { name: 'Check node status', status: 'completed', output: 'All 3 nodes healthy' },
    { name: 'Check pod status', status: 'completed', output: '24/24 pods running' },
    { name: 'Scan cluster', status: 'completed', output: 'No issues found' },
  ],
};

// ============================================================================
// Module Sources
// ============================================================================

export const mockModuleSources = [
  {
    id: 1,
    name: 'Official BNK Modules',
    source_type: 'git',
    url: 'https://github.com/f5/bnk-modules.git',
    branch: 'main',
    is_active: true,
    last_synced_at: '2026-02-18T00:00:00Z',
    module_count: 12,
  },
];

// ============================================================================
// Parallel Execution
// ============================================================================

export const mockParallelExecution = {
  id: 1,
  status: 'completed',
  action: 'apply',
  current_layer: 3,
  total_layers: 3,
  successful_modules: [1, 2],
  failed_modules: [],
  skipped_modules: [],
};

export const mockParallelExecutionPending = {
  execution_id: 1,
  status: 'pending',
  action: 'apply',
  total_layers: 3,
};

// ============================================================================
// Variable Mappings
// ============================================================================

export const mockVariableMappings = [
  {
    id: 1,
    source_module_id: 1,
    source_output: 'vpc_id',
    target_module_id: 2,
    target_variable: 'vpc_id',
    created_at: '2026-02-01T00:00:00Z',
  },
];

// ============================================================================
// Audit Logs (not directly API-based fixture but useful)
// ============================================================================

export const mockAuditEntries = [
  {
    id: 1,
    action: 'project.create',
    user: 'admin',
    resource_type: 'project',
    resource_id: '1',
    details: { name: 'test-project' },
    timestamp: '2026-02-18T10:00:00Z',
    ip_address: '10.0.0.1',
  },
  {
    id: 2,
    action: 'module.apply',
    user: 'admin',
    resource_type: 'module',
    resource_id: '1',
    details: { module_name: 'vpc', project_id: 1 },
    timestamp: '2026-02-18T10:05:00Z',
    ip_address: '10.0.0.1',
  },
];
