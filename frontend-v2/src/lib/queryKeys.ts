/**
 * Centralized Query Key Factory
 *
 * This file provides a type-safe, hierarchical structure for React Query keys.
 * Using this pattern enables:
 * - Easy invalidation of related queries
 * - Type safety for query keys
 * - Clear organization of data fetching
 * - Prevents typos and inconsistencies
 *
 * Example usage:
 * - Invalidate all projects: queryClient.invalidateQueries({ queryKey: queryKeys.projects.all })
 * - Invalidate specific project: queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(6) })
 * - Invalidate all modules for a project: queryClient.invalidateQueries({ queryKey: queryKeys.modules.byProject(6) })
 *
 * @see https://tkdodo.eu/blog/effective-react-query-keys
 */

export const queryKeys = {
  // Projects hierarchy
  projects: {
    all: ['projects'] as const,
    lists: () => ['projects', 'list'] as const,
    list: () => ['projects', 'list'] as const,
    details: () => ['projects', 'detail'] as const,
    detail: (id: number) => ['projects', 'detail', id] as const,
    variableDefaults: (id: number) => ['projects', 'detail', id, 'variable-defaults'] as const,
    variables: (id: number) => ['projects', 'detail', id, 'variables'] as const,
  },

  // Modules hierarchy
  modules: {
    all: ['modules'] as const,

    // Library modules (catalog)
    library: {
      all: ['module-library'] as const,
      lists: () => ['module-library', 'list'] as const,
      list: (params?: { category?: string; provider?: string; search?: string }) =>
        ['module-library', 'list', params] as const,
      details: () => ['module-library', 'detail'] as const,
      detail: (id: number) => ['module-library', 'detail', id] as const,
      variables: (id: number) => ['module-library', 'detail', id, 'variables'] as const,
      categories: () => ['module-library', 'categories'] as const,
      providers: () => ['module-library', 'providers'] as const,
      variableMappingTemplates: () => ['module-library', 'variable-mapping-templates'] as const,
    },

    // Project modules (instances)
    project: {
      all: ['project-modules'] as const,
      lists: () => ['project-modules', 'list'] as const,
      byProject: (projectId: number) => ['project-modules', 'list', { projectId }] as const,
      details: () => ['project-modules', 'detail'] as const,
      detail: (id: number) => ['project-modules', 'detail', id] as const,
      status: (id: number) => ['project-modules', 'detail', id, 'status'] as const,
      stateHistory: (id: number, limit: number) =>
        ['project-modules', 'detail', id, 'state-history', limit] as const,
      actions: (id: number) => ['project-modules', 'detail', id, 'actions'] as const,
      reports: (id: number) => ['project-modules', 'detail', id, 'reports'] as const,
      reportContent: (id: number, path: string) =>
        ['project-modules', 'detail', id, 'reports', 'content', path] as const,
      variableMappings: (id: number) => ['project-modules', 'detail', id, 'variable-mappings'] as const,
    },
  },

  // Tasks hierarchy
  tasks: {
    all: ['tasks'] as const,
    lists: () => ['tasks', 'list'] as const,
    list: (filters?: { project_id?: number; type?: string; status?: string }) =>
      ['tasks', 'list', filters] as const,
    details: () => ['tasks', 'detail'] as const,
    detail: (id: number) => ['tasks', 'detail', id] as const,
    stats: () => ['tasks', 'stats'] as const,
  },

  // Discovery hierarchy
  discovery: {
    all: ['discovery'] as const,
    byProject: (projectId: number) => ['discovery', 'project', projectId] as const,
    job: (projectId: number, jobId: number) => ['discovery', 'project', projectId, 'job', jobId] as const,
  },

  // Drift detection hierarchy
  drift: {
    all: ['drift'] as const,
    stats: () => ['drift', 'stats'] as const,
    settings: (projectId: number) => ['drift', 'settings', projectId] as const,
    checks: (projectId: number) => ['drift', 'checks', projectId] as const,
    check: (checkId: number) => ['drift', 'check', checkId] as const,
    summary: (projectId: number) => ['drift', 'summary', projectId] as const,
  },

  // Kubernetes hierarchy
  k8s: {
    all: ['k8s'] as const,
    resourceTypes: () => ['k8s', 'resource-types'] as const,
    clusters: {
      all: ['k8s', 'clusters'] as const,
      byProject: (projectId: number) => ['k8s', 'clusters', 'project', projectId] as const,
      detail: (clusterId: number) => ['k8s', 'clusters', clusterId] as const,
      namespaces: (clusterId: number) => ['k8s', 'clusters', clusterId, 'namespaces'] as const,
      // Resources — use partial key for broad invalidation
      allResources: (clusterId: number) => ['k8s', 'clusters', clusterId, 'resources'] as const,
      resources: (clusterId: number, resourceType: string, params?: Record<string, string | undefined>) =>
        ['k8s', 'clusters', clusterId, 'resources', resourceType, params] as const,
      // BNK data
      bnkData: (clusterId: number, params?: Record<string, string | undefined>) =>
        ['k8s', 'clusters', clusterId, 'f5bnk', 'data', params] as const,
      // A2A agent discovery
      a2aAgents: (clusterId: number, params?: Record<string, string | boolean | undefined>) =>
        ['k8s', 'clusters', clusterId, 'f5bnk', 'a2a', 'agents', params] as const,
      // BNK upgrade (prefix key invalidates all sub-keys: versions, current, history, detail)
      bnkUpgrade: (clusterId: number) => ['k8s', 'clusters', clusterId, 'bnk', 'upgrade'] as const,
      bnkUpgradeVersions: (clusterId: number) => ['k8s', 'clusters', clusterId, 'bnk', 'upgrade', 'versions'] as const,
      bnkCurrentVersion: (clusterId: number) => ['k8s', 'clusters', clusterId, 'bnk', 'upgrade', 'current'] as const,
      bnkUpgradeHistory: (clusterId: number) => ['k8s', 'clusters', clusterId, 'bnk', 'upgrade', 'history'] as const,
      bnkUpgradeDetail: (clusterId: number, upgradeId: number) => ['k8s', 'clusters', clusterId, 'bnk', 'upgrade', upgradeId] as const,
      // Connectivity probes
      connectivity: (clusterId: number) => ['k8s', 'clusters', clusterId, 'connectivity'] as const,
      batchConnectivity: () => ['k8s', 'clusters', 'connectivity'] as const,
      // Scan
      scan: (clusterId: number) => ['k8s', 'clusters', clusterId, 'scan'] as const,
      // Tunnel
      tunnel: (clusterId: number) => ['k8s', 'clusters', clusterId, 'tunnel'] as const,
      // Pod operations
      podLogs: (clusterId: number, podName: string, namespace?: string) =>
        ['k8s', 'clusters', clusterId, 'pods', podName, 'logs', namespace] as const,
      podContainers: (clusterId: number, podName: string, namespace?: string) =>
        ['k8s', 'clusters', clusterId, 'pods', podName, 'containers', namespace] as const,
      // Resource describe
      describeResource: (clusterId: number, resourceType: string, resourceName: string, namespace?: string) =>
        ['k8s', 'clusters', clusterId, 'resources', resourceType, resourceName, 'describe', namespace] as const,
      // Deployment rollout
      rolloutHistory: (clusterId: number, deploymentName: string, namespace?: string) =>
        ['k8s', 'clusters', clusterId, 'deployments', deploymentName, 'rollout', 'history', namespace] as const,
      rolloutStatus: (clusterId: number, deploymentName: string, namespace?: string) =>
        ['k8s', 'clusters', clusterId, 'deployments', deploymentName, 'rollout', 'status', namespace] as const,
      // Node count (inline query in KubernetesV2.tsx)
      nodeCount: (clusterId: number) => ['k8s', 'clusters', clusterId, 'node-count'] as const,
      // Events
      events: (clusterId: number, params?: Record<string, string | undefined>) =>
        ['k8s', 'clusters', clusterId, 'events', params] as const,
      // Metrics
      podMetrics: (clusterId: number, params?: Record<string, string | undefined>) =>
        ['k8s', 'clusters', clusterId, 'metrics', 'pods', params] as const,
      nodeMetrics: (clusterId: number, params?: Record<string, string | undefined>) =>
        ['k8s', 'clusters', clusterId, 'metrics', 'nodes', params] as const,
      // Adaptive module plan
      adaptiveModules: (clusterId: number, templateSlug?: string) =>
        ['k8s', 'clusters', clusterId, 'adaptive-modules', templateSlug || 'default'] as const,
      // CNF: CRD discovery (D-018 P1)
      crds: (clusterId: number, params?: { group?: string[] }) =>
        ['k8s', 'clusters', clusterId, 'crds', params] as const,
      // CNF: Namespace topology graph (D-018 P4)
      topology: (clusterId: number, namespace: string) =>
        ['k8s', 'clusters', clusterId, 'topology', namespace] as const,
      // DPF (NVIDIA DPU infrastructure)
      dpf: (clusterId: number) => ['k8s', 'clusters', clusterId, 'dpf'] as const,
      dpfDetect: (clusterId: number) => ['k8s', 'clusters', clusterId, 'dpf', 'detect'] as const,
      dpfData: (clusterId: number) => ['k8s', 'clusters', clusterId, 'dpf', 'data'] as const,
      dpfHealth: (clusterId: number) => ['k8s', 'clusters', clusterId, 'dpf', 'health'] as const,
    },
    tunnels: () => ['k8s', 'tunnels'] as const,
  },

  // Deployments hierarchy (module deployment history)
  deployments: {
    all: ['deployments'] as const,
    byModule: (moduleId: number) => ['deployments', 'module', moduleId] as const,
    detail: (deploymentId: number) => ['deployments', 'detail', deploymentId] as const,
  },

  // Helm hierarchy
  helm: {
    all: ['helm'] as const,
    releases: {
      all: ['helm-releases'] as const,
      byCluster: (clusterId: number | null, namespace?: string, allNamespaces?: boolean) =>
        ['helm-releases', clusterId, namespace, allNamespaces] as const,
      detail: (clusterId: number | null, releaseName: string | null, namespace?: string) =>
        ['helm-release', clusterId, releaseName, namespace] as const,
      history: (clusterId: number | null, releaseName: string | null, namespace?: string) =>
        ['helm-history', clusterId, releaseName, namespace] as const,
      values: (clusterId: number | null, releaseName: string | null, namespace?: string, allValues?: boolean) =>
        ['helm-values', clusterId, releaseName, namespace, allValues] as const,
      compare: (clusterId: number | null, releaseName: string | null, revision1: number | null, revision2: number | null, namespace?: string) =>
        ['helm-compare', clusterId, releaseName, revision1, revision2, namespace] as const,
    },
    repos: {
      all: ['helm-repositories'] as const,
    },
    charts: {
      search: (keyword: string, repository?: string) => ['helm-charts-search', keyword, repository] as const,
      browse: (repository?: string | null) => ['helm-charts-browse', repository] as const,
      uploaded: ['helm-uploaded-charts'] as const,
      values: (chartId: number | null) => ['helm-chart-values', chartId] as const,
    },
  },

  // Operators hierarchy
  operators: {
    all: ['operators'] as const,
    list: () => ['operators', 'list'] as const,
    detail: (id: string) => ['operators', 'detail', id] as const,
    connectivityModes: () => ['operators', 'connectivity-modes'] as const,
  },

  // Fleet hierarchy
  fleet: {
    all: ['fleet'] as const,
    health: () => ['fleet', 'health'] as const,
    compare: (sourceId: number, targetId: number) => ['fleet', 'compare', sourceId, targetId] as const,
    members: (params?: { label?: string[]; member_type?: string; group_by?: string }) =>
      ['fleet', 'members', params] as const,
    // Phase 2: targeting + bulk-ops
    targets: () => ['fleet', 'targets'] as const,
    fleetMembers: (fleetId: number) => ['fleet', 'fleet-members', fleetId] as const,
    decision: (decisionId: number) => ['fleet', 'decisions', decisionId] as const,
    bulkRuns: (params?: { fleet_id?: number; status?: string }) =>
      ['fleet', 'bulk-ops', 'list', params] as const,
    bulkRun: (runId: number) => ['fleet', 'bulk-ops', runId] as const,
    // Phase 3: policies + compliance
    policies: () => ['fleet', 'policies'] as const,
    policy: (policyId: number) => ['fleet', 'policies', policyId] as const,
    policyCompliance: (policyId: number) => ['fleet', 'policies', policyId, 'compliance'] as const,
    policyEvaluations: (policyId: number) => ['fleet', 'policies', policyId, 'evaluations'] as const,
    // Phase 6 Slice B: per-fleet rollup
    rollup: (fleetId: number) => ['fleet', 'targets', fleetId, 'rollup'] as const,
    rollups: (ids: number[]) => ['fleet', 'targets', 'rollup', 'batch', ids] as const,
    // Phase 6 Slice D: operation strategies
    strategies: (fleetId?: number) => ['fleet', 'strategies', fleetId] as const,
    strategy: (strategyId: number) => ['fleet', 'strategies', 'detail', strategyId] as const,
    // P6 follow-up: selector builder
    labelVocabulary: () => ['fleet', 'label-vocabulary'] as const,
  },

  // Stacks hierarchy (already partially used via useStacks)
  stacks: {
    all: ['stacks'] as const,
    templates: () => ['stacks', 'templates'] as const,
    instances: {
      all: ['stacks', 'instances'] as const,
      byProject: (projectId: number) => ['stacks', 'instances', 'project', projectId] as const,
      detail: (instanceId: number) => ['stacks', 'instances', instanceId] as const,
    },
  },

  // Snapshots hierarchy
  snapshots: {
    all: ['snapshots'] as const,
    list: (projectId: number | undefined, limit?: number, offset?: number) =>
      ['snapshots', projectId, limit, offset] as const,
    detail: (snapshotId: number | undefined) => ['snapshot', snapshotId] as const,
  },

  // System / Settings hierarchy
  system: {
    all: ['system'] as const,
    health: () => ['system', 'health'] as const,
    processMetrics: () => ['system', 'process-metrics'] as const,
    queueMetrics: () => ['system', 'queue-metrics'] as const,
    performance: () => ['system', 'performance'] as const,
    errors: (limit?: number) => ['system', 'errors', limit] as const,
    databaseStats: () => ['system', 'database', 'stats'] as const,
    containerStatus: () => ['system', 'containers', 'status'] as const,
    version: () => ['system-version'] as const,
    upgradeStatus: () => ['system', 'upgrade', 'status'] as const,
    settings: () => ['settings'] as const,
    systemDefaults: () => ['system-defaults'] as const,
    defaultsStatus: () => ['defaults-status'] as const,
    backupStatus: () => [...queryKeys.system.all, 'backup', 'status'] as const,
    maintenanceStatus: () => [...queryKeys.system.all, 'maintenance'] as const,
  },

  // Registry hierarchy
  registry: {
    all: ['registry'] as const,
    search: (query: string, provider?: string, verified?: boolean, limit?: number, offset?: number) =>
      ['registry-search', query, provider, verified, limit, offset] as const,
    module: (namespace: string | null, name: string | null, provider: string | null, useTerraformRegistry?: boolean) =>
      ['registry-module', namespace, name, provider, useTerraformRegistry] as const,
    moduleVersions: (namespace: string | null, name: string | null, provider: string | null, useTerraformRegistry?: boolean) =>
      ['registry-module-versions', namespace, name, provider, useTerraformRegistry] as const,
    moduleUpdates: (moduleId: number | null) => ['module-updates', moduleId] as const,
  },

  // Secrets hierarchy
  secrets: {
    all: ['project-secrets'] as const,
    list: (projectId: number) => ['project-secrets', 'list', projectId] as const,
    required: (projectId: number, stackSlug?: string) =>
      ['project-secrets', 'required', projectId, stackSlug] as const,
  },

  // Module sources hierarchy
  moduleSources: {
    all: ['module-sources'] as const,
    lists: () => ['module-sources', 'list'] as const,
    list: (filters?: { source_type?: string; is_active?: boolean }) =>
      ['module-sources', 'list', filters] as const,
    details: () => ['module-sources', 'detail'] as const,
    detail: (id: number) => ['module-sources', 'detail', id] as const,
    modules: (id: number) => ['module-sources', 'detail', id, 'modules'] as const,
  },

  blueprintCatalog: {
    all: ['blueprint-catalog'] as const,
    sources: () => ['blueprint-catalog', 'sources'] as const,
    sourceList: (filters?: { source_type?: string; is_active?: boolean }) =>
      ['blueprint-catalog', 'sources', filters] as const,
    releases: () => ['blueprint-catalog', 'releases'] as const,
    releaseList: (filters?: {
      source_id?: number;
      blueprint_id?: string;
      release_state?: string;
      validation_state?: string;
      is_active?: boolean;
    }) => ['blueprint-catalog', 'releases', filters] as const,
  },

  // Notifications hierarchy
  notifications: {
    all: ['notifications'] as const,
    list: (unreadOnly?: boolean) => ['notifications', unreadOnly] as const,
    infinite: (filters: { unreadOnly?: boolean; category?: string; severity?: string }) =>
      ['notifications', 'infinite', filters] as const,
    unreadCount: () => ['notifications', 'unread-count'] as const,
  },

  // Auth hierarchy
  auth: {
    all: ['auth'] as const,
    me: () => ['auth', 'me'] as const,
    users: () => ['auth', 'users'] as const,
  },

  // Config Promotion hierarchy
  configPromotion: {
    all: ['config-promotion'] as const,
    history: (limit?: number, offset?: number) => ['config-promotion', 'history', limit, offset] as const,
  },

  // Runbooks hierarchy
  runbooks: {
    all: ['runbooks'] as const,
    list: () => ['runbooks', 'list'] as const,
  },

  // Execution plans hierarchy
  execution: {
    all: ['execution'] as const,
    plan: (projectId: number) => ['execution', 'plan', projectId] as const,
    parallel: (executionId: number) => ['execution', 'parallel', executionId] as const,
    allParallel: (projectId: number) => ['execution', 'parallel', 'project', projectId] as const,
  },

  // Dashboard stats hierarchy
  dashboard: {
    all: ['dashboard'] as const,
    stats: () => ['dashboard', 'stats'] as const,
    blueprints: () => ['dashboard', 'blueprints'] as const,
    globalDrift: () => ['global-drift-summary'] as const,
    recentDrift: (limit?: number) => ['recent-drifted', limit] as const,
  },

  // SSH Credentials hierarchy
  sshCredentials: {
    all: ['ssh-credentials'] as const,
    lists: () => ['ssh-credentials', 'list'] as const,
    list: () => ['ssh-credentials', 'list'] as const,
    details: () => ['ssh-credentials', 'detail'] as const,
    detail: (id: number) => ['ssh-credentials', 'detail', id] as const,
  },

  // Container Registries hierarchy
  containerRegistries: {
    all: ['container-registries'] as const,
    lists: () => ['container-registries', 'list'] as const,
    list: () => ['container-registries', 'list'] as const,
    details: () => ['container-registries', 'detail'] as const,
    detail: (id: number) => ['container-registries', 'detail', id] as const,
  },

  // Benchmarks hierarchy (Phase 2: AI Performance Dashboard)
  benchmarks: {
    all: ['benchmarks'] as const,
    configs: {
      all: ['benchmarks', 'configs'] as const,
      list: () => ['benchmarks', 'configs', 'list'] as const,
      detail: (configId: number) => ['benchmarks', 'configs', 'detail', configId] as const,
    },
    runs: {
      all: ['benchmarks', 'runs'] as const,
      list: (params?: { proxy?: string; tool?: string; model?: string; status?: string; limit?: number; offset?: number }) =>
        ['benchmarks', 'runs', 'list', params] as const,
      detail: (runId: number) => ['benchmarks', 'runs', 'detail', runId] as const,
    },
    agents: {
      detail: (agentId: number) => ['benchmarks', 'agents', 'detail', agentId] as const,
    },
    agentHosts: {
      all: ['benchmarks', 'agent-hosts'] as const,
      list: (projectId?: number) => ['benchmarks', 'agent-hosts', 'list', projectId] as const,
      detail: (hostId: number) => ['benchmarks', 'agent-hosts', 'detail', hostId] as const,
    },
    agentHostCandidates: {
      list: (projectId: number) => ['benchmarks', 'agent-host-candidates', projectId] as const,
    },
    compare: (runIds: number[]) => ['benchmarks', 'compare', ...runIds] as const,
    summary: () => ['benchmarks', 'summary'] as const,
    targets: {
      all: ['benchmarks', 'targets'] as const,
      list: (params?: { status?: string; cluster_id?: number }) =>
        ['benchmarks', 'targets', 'list', params] as const,
      detail: (targetId: number) => ['benchmarks', 'targets', 'detail', targetId] as const,
      proxies: (targetId: number) => ['benchmarks', 'targets', targetId, 'proxies'] as const,
    },
    scenarios: () => ['benchmarks', 'scenarios'] as const,
    runGroups: {
      all: ['benchmarks', 'run-groups'] as const,
      detail: (groupId: number) => ['benchmarks', 'run-groups', 'detail', groupId] as const,
    },
    trends: (params?: { target_id?: number; proxy?: string; scenario_key?: string; config_id?: number; limit?: number }) =>
      ['benchmarks', 'trends', params] as const,
  },

  // MCP server
  mcp: {
    status: () => ['mcp', 'status'] as const,
  },

  // AI Gateway Observability (Tier 1: request analytics — Loki-backed)
  llmObservability: {
    all: ['llm-observability'] as const,
    stats: (clusterId: number, params?: Record<string, string | undefined>) =>
      ['llm-observability', clusterId, 'stats', params] as const,
    histogram: (clusterId: number, params?: Record<string, string | undefined>) =>
      ['llm-observability', clusterId, 'histogram', params] as const,
    rankings: (clusterId: number, params?: Record<string, string | undefined>) =>
      ['llm-observability', clusterId, 'rankings', params] as const,
    providerUsage: (clusterId: number, params?: Record<string, string | undefined>) =>
      ['llm-observability', clusterId, 'provider-usage', params] as const,
    logs: (clusterId: number, params?: Record<string, string | undefined>) =>
      ['llm-observability', clusterId, 'logs', params] as const,
    filterData: (clusterId: number, params?: Record<string, string | undefined>) =>
      ['llm-observability', clusterId, 'filterdata', params] as const,
  },

  // DPU Provisioning (Blueprint 1) — Bluefield software image catalog, DPUs
  dpuProvisioning: {
    all: ['dpu-provisioning'] as const,
    bluefieldImages: {
      all: ['dpu-provisioning', 'bluefield-images'] as const,
      detail: (imageId: number) => ['dpu-provisioning', 'bluefield-images', imageId] as const,
      default: ['dpu-provisioning', 'bluefield-images', 'default'] as const,
    },
    bfConfTemplates: {
      all: ['dpu-provisioning', 'bf-conf-templates'] as const,
    },
    dpus: {
      byProject: (projectId: number) => ['dpu-provisioning', 'dpus', { projectId }] as const,
      detail: (projectId: number, dpuId: number) =>
        ['dpu-provisioning', 'dpus', { projectId }, dpuId] as const,
    },
    settings: {
      byProject: (projectId: number) => ['dpu-provisioning', 'settings', { projectId }] as const,
    },
    connectivityMatrix: {
      byProject: (projectId: number) =>
        ['dpu-provisioning', 'connectivity-matrix', { projectId }] as const,
    },
  },

  // Release Sources (BNK catalog management — ADR-494)
  releaseSources: {
    all: ['release-sources'] as const,
    list: () => ['release-sources', 'list'] as const,
    detail: (id: number) => ['release-sources', 'detail', id] as const,
    tags: (id: number) => ['release-sources', 'tags', id] as const,
  },

  // Bare Metal DPU Deployment
  bareMetal: {
    all: ['bare-metal'] as const,
    hosts: {
      all: ['bare-metal', 'hosts'] as const,
      byProject: (projectId: number) => ['bare-metal', 'hosts', { projectId }] as const,
      detail: (projectId: number, hostId: number) =>
        ['bare-metal', 'hosts', { projectId }, hostId] as const,
      discoveryResult: (projectId: number, hostId: number) =>
        ['bare-metal', 'hosts', { projectId }, hostId, 'discovery-result'] as const,
    },
    deployments: {
      all: ['bare-metal', 'deployments'] as const,
      byProject: (projectId: number) => ['bare-metal', 'deployments', { projectId }] as const,
      byHost: (projectId: number, hostId: number) =>
        ['bare-metal', 'deployments', { projectId, hostId }] as const,
      detail: (projectId: number, deploymentId: number) =>
        ['bare-metal', 'deployments', { projectId }, deploymentId] as const,
      stepLogs: (projectId: number, deploymentId: number, stepIndex: number) =>
        ['bare-metal', 'deployments', { projectId }, deploymentId, 'steps', stepIndex] as const,
    },
    releases: {
      all: ['bare-metal', 'releases'] as const,
      detail: (releaseId: number) => ['bare-metal', 'releases', releaseId] as const,
    },
  },

  // Proxy Migration (D-021 P3)
  proxyMigrations: {
    byCluster: (clusterId: number) => ['proxy-migrations', clusterId] as const,
    detail: (clusterId: number, migrationId: number) =>
      ['proxy-migrations', clusterId, migrationId] as const,
  },

  // Drift detection hierarchy (extended)
  // NOTE: additional keys beyond base drift.* for hooks in useDrift.ts
  driftExtended: {
    settings: (projectId: number | undefined) => ['drift-settings', projectId] as const,
    checks: (projectId: number | undefined, moduleId?: number, limit?: number, offset?: number) =>
      ['drift-checks', projectId, moduleId, limit, offset] as const,
    checksAll: ['drift-checks'] as const,
    check: (checkId: number | undefined) => ['drift-check', checkId] as const,
    projectSummary: (projectId: number | undefined) => ['project-drift-summary', projectId] as const,
    globalSummary: () => ['global-drift-summary'] as const,
    recentDrifted: (limit?: number) => ['recent-drifted', limit] as const,
    clusterStatus: (clusterId: number | undefined) => ['cluster-drift-status', clusterId] as const,
    stats: (projectId?: number, days?: number) => ['drift-stats', projectId, days] as const,
  },
} as const;

/**
 * Helper type to extract query key type from the factory
 * Usage: type ProjectsKey = QueryKey<typeof queryKeys.projects.all>
 */
export type QueryKey<T extends readonly unknown[]> = T;
