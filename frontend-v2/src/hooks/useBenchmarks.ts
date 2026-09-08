/**
 * React Query hooks for benchmarks (Phase 2 + Phase 4b: LLM Inference Load Testing Dashboard)
 */
import { useEffect, useRef, useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { notify, notifyError } from '@/lib/notify';
import { POLL_INTERVALS } from '@/lib/constants';
import type {
  AgentHostScanRequest,
  BenchmarkAgentHostCreate,
  BenchmarkConfigCreate,
  BenchmarkConfigUpdate,
  BenchmarkRunCreate,
  BenchmarkTargetCreate,
  BenchmarkTargetUpdate,
  BenchmarkTrendsParams,
  BenchmarkWSMessage,
  DiscoverTargetsRequest,
  ProxyDeployRequest,
  ProxyDeploymentUpdate,
  ScenarioRunRequest,
  TriggerRunRequest,
} from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ============================================================================
// Config Queries / Mutations
// ============================================================================

export const useBenchmarkConfigs = (tool?: string) =>
  useQuery({
    queryKey: queryKeys.benchmarks.configs.list(),
    queryFn: () => api.listConfigs(tool ? { tool } : undefined),
  });

export const useBenchmarkConfig = (configId: number | undefined) =>
  useQuery({
    queryKey: queryKeys.benchmarks.configs.detail(configId!),
    queryFn: () => api.getConfig(configId!),
    enabled: !!configId,
  });

export const useCreateBenchmarkConfig = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (data: BenchmarkConfigCreate) => api.createConfig(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.configs.all });
    },
    onError: (error) => notifyError(error),
  });
};

export const useUpdateBenchmarkConfig = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ configId, data }: { configId: number; data: BenchmarkConfigUpdate }) =>
      api.updateConfig(configId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.configs.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.configs.detail(variables.configId) });
    },
    onError: (error) => notifyError(error),
  });
};

export const useDeleteBenchmarkConfig = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (configId: number) => api.deleteConfig(configId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.configs.all });
    },
    onError: (error) => notifyError(error),
  });
};

// ============================================================================
// Run Queries / Mutations
// ============================================================================

export const useBenchmarkRuns = (params?: {
  proxy?: string;
  tool?: string;
  model?: string;
  status?: string;
  limit?: number;
  offset?: number;
  pollingEnabled?: boolean;
}) => {
  const { pollingEnabled, ...queryParams } = params ?? {};
  return useQuery({
    queryKey: queryKeys.benchmarks.runs.list(queryParams),
    queryFn: () => api.listRuns(queryParams),
    refetchInterval: pollingEnabled ? POLL_INTERVALS.STANDARD : false,
  });
};

export const useBenchmarkRun = (runId: number | undefined, pollingEnabled = false) =>
  useQuery({
    queryKey: queryKeys.benchmarks.runs.detail(runId!),
    queryFn: () => api.getRun(runId!),
    enabled: !!runId,
    refetchInterval: pollingEnabled ? POLL_INTERVALS.FAST : false,
  });

export const useCreateBenchmarkRun = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (data: BenchmarkRunCreate) => api.createRun(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.summary() });
      notify.success('Benchmark run created', undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

export const useCancelBenchmarkRun = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (runId: number) => api.cancelRun(runId),
    onSuccess: (_, runId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.detail(runId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.summary() });
      notify.success('Benchmark run cancelled', undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

export const useDeleteBenchmarkRun = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (runId: number) => api.deleteRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.summary() });
      notify.success('Benchmark run deleted', undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

// ============================================================================
// Baseline + Trends
// ============================================================================

export const useSetBenchmarkRunBaseline = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (runId: number) => api.setRunBaseline(runId),
    onSuccess: (_, runId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.detail(runId) });
      queryClient.invalidateQueries({ queryKey: ['benchmarks', 'trends'] });
      notify.success('Run marked as baseline', undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

export const useUnsetBenchmarkRunBaseline = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (runId: number) => api.unsetRunBaseline(runId),
    onSuccess: (_, runId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.detail(runId) });
      queryClient.invalidateQueries({ queryKey: ['benchmarks', 'trends'] });
      notify.success('Baseline cleared', undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

export const useBenchmarkTrends = (params: BenchmarkTrendsParams | undefined, enabled = true) =>
  useQuery({
    queryKey: queryKeys.benchmarks.trends(params),
    queryFn: () => api.getTrends(params),
    enabled,
  });

// ============================================================================
// Agent Queries
// ============================================================================

export const useBenchmarkAgents = () =>
  useQuery({
    queryKey: ['benchmarks', 'agents', 'list'] as const,
    queryFn: () => api.listAgents(),
    refetchInterval: POLL_INTERVALS.STANDARD,
  });

export const useDeleteBenchmarkAgent = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (agentId: number) => api.deleteAgent(agentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['benchmarks', 'agents', 'list'] });
      notify.success('Agent deregistered', undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

// ============================================================================
// Agent Host Hooks (Slice 1) — project-scoped managed remote hosts
// ============================================================================

export const useBenchmarkAgentHosts = (projectId?: number) =>
  useQuery({
    queryKey: queryKeys.benchmarks.agentHosts.list(projectId),
    queryFn: () => api.listAgentHosts(projectId !== undefined ? { project_id: projectId } : undefined),
    // Poll while any host is scanning or provisioning
    refetchInterval: (query) => {
      const hosts = query.state.data;
      if (hosts?.some((h) => h.provision_status === 'scanning' || h.provision_status === 'provisioning')) {
        return POLL_INTERVALS.FAST;
      }
      return false;
    },
  });

export const useBenchmarkAgentHost = (hostId: number | undefined) =>
  useQuery({
    queryKey: queryKeys.benchmarks.agentHosts.detail(hostId!),
    queryFn: () => api.getAgentHost(hostId!),
    enabled: !!hostId,
    // Poll while scanning or provisioning in progress
    refetchInterval: (query) => {
      const host = query.state.data;
      return host?.provision_status === 'scanning' || host?.provision_status === 'provisioning'
        ? POLL_INTERVALS.FAST
        : false;
    },
  });

export const useCreateBenchmarkAgentHost = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (data: BenchmarkAgentHostCreate) => api.createAgentHost(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.agentHosts.all });
      notify.success(`Registered remote host "${result.name}"`, undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

export const useDeleteBenchmarkAgentHost = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (hostId: number) => api.deleteAgentHost(hostId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.agentHosts.all });
      notify.success('Remote host removed', undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

export const useScanBenchmarkAgentHost = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ hostId, data }: { hostId: number; data?: AgentHostScanRequest }) =>
      api.scanAgentHost(hostId, data),
    onSuccess: async (_result, variables) => {
      // Use refetchQueries (not invalidateQueries) so the "scanning" provision_status
      // is in cache before the polling refetchInterval evaluator next runs.
      await queryClient.refetchQueries({
        queryKey: queryKeys.benchmarks.agentHosts.detail(variables.hostId),
      });
      await queryClient.refetchQueries({
        queryKey: queryKeys.benchmarks.agentHosts.list(),
      });
      notify.info('SSH scan dispatched — polling for results…', undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

export const useProvisionBenchmarkAgentHost = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (hostId: number) => api.provisionAgentHost(hostId),
    onSuccess: async (_result, hostId) => {
      // Use refetchQueries so the "provisioning" status is in cache before the
      // polling refetchInterval evaluator fires.
      await queryClient.refetchQueries({
        queryKey: queryKeys.benchmarks.agentHosts.detail(hostId),
      });
      await queryClient.refetchQueries({
        queryKey: queryKeys.benchmarks.agentHosts.list(),
      });
      notify.info(
        'SSH provisioning dispatched — polling for progress…',
        undefined,
        { category: 'general' },
      );
    },
    onError: (error) => notifyError(error),
  });
};

// ============================================================================
// Agent Host Candidates (Slice 5) — project host/jumphost picker
// ============================================================================

export const useAgentHostCandidates = (projectId: number | undefined) =>
  useQuery({
    queryKey: queryKeys.benchmarks.agentHostCandidates.list(projectId!),
    queryFn: () => api.listAgentHostCandidates(projectId!),
    enabled: !!projectId,
  });

export const useImportAwsJumphost = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ projectId, moduleId }: { projectId: number; moduleId: number }) =>
      api.importAwsJumphost({ project_id: projectId, module_id: moduleId }),
    onSuccess: (_result, variables) => {
      // Invalidate candidates so the picker refreshes with the new credential id
      queryClient.invalidateQueries({
        queryKey: queryKeys.benchmarks.agentHostCandidates.list(variables.projectId),
      });
      notify.success('AWS jumphost credential imported', undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

// ============================================================================
// Comparison & Summary
// ============================================================================

export const useBenchmarkCompare = (runIds: number[]) =>
  useQuery({
    queryKey: queryKeys.benchmarks.compare(runIds),
    queryFn: () => api.compareRuns({ run_ids: runIds }),
    enabled: runIds.length >= 2,
  });

export const useBenchmarkSummary = () =>
  useQuery({
    queryKey: queryKeys.benchmarks.summary(),
    queryFn: () => api.getSummary(),
    refetchInterval: POLL_INTERVALS.BACKGROUND,
  });

/** Transitional proxy statuses that trigger auto-polling (Phase 4c). */
const PROXY_TRANSITIONAL_STATES = new Set(['pending', 'deploying', 'uninstalling']);

// ============================================================================
// Target Queries / Mutations (Phase 4b)
// ============================================================================

export const useBenchmarkTargets = (params?: { status?: string; cluster_id?: number }) =>
  useQuery({
    queryKey: queryKeys.benchmarks.targets.list(params),
    queryFn: () => api.listTargets(params),
  });

export const useBenchmarkTarget = (targetId: number | undefined) =>
  useQuery({
    queryKey: queryKeys.benchmarks.targets.detail(targetId!),
    queryFn: () => api.getTarget(targetId!),
    enabled: !!targetId,
    // Phase 4c: Auto-poll when any proxy on the target is in a transitional state
    refetchInterval: (query) => {
      const detail = query.state.data as { proxy_deployments?: Array<{ status: string }> } | undefined;
      if (detail?.proxy_deployments?.some(p => PROXY_TRANSITIONAL_STATES.has(p.status))) {
        return POLL_INTERVALS.FAST;
      }
      return false;
    },
  });

export const useCreateBenchmarkTarget = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (data: BenchmarkTargetCreate) => api.createTarget(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.all });
    },
    onError: (error) => notifyError(error),
  });
};

export const useUpdateBenchmarkTarget = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ targetId, data }: { targetId: number; data: BenchmarkTargetUpdate }) =>
      api.updateTarget(targetId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.detail(variables.targetId) });
    },
    onError: (error) => notifyError(error),
  });
};

export const useDeleteBenchmarkTarget = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (targetId: number) => api.deleteTarget(targetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.all });
    },
    onError: (error) => notifyError(error),
  });
};

export const useValidateBenchmarkTarget = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (targetId: number) => api.validateTarget(targetId),
    onSuccess: (_, targetId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.detail(targetId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.all });
      notify.success('Target validation complete', undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

// ============================================================================
// Proxy Deployment Queries / Mutations (Phase 4b + Phase 4c polling)
// ============================================================================

export const useProxyDeployments = (targetId: number | undefined) =>
  useQuery({
    queryKey: queryKeys.benchmarks.targets.proxies(targetId!),
    queryFn: () => api.listProxyDeployments(targetId!),
    enabled: !!targetId,
    // Phase 4c: Auto-poll when any proxy is in a transitional state
    refetchInterval: (query) => {
      const proxies = query.state.data;
      if (proxies?.some(p => PROXY_TRANSITIONAL_STATES.has(p.status))) {
        return POLL_INTERVALS.FAST;
      }
      return false;
    },
  });

export const useDeployProxy = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ targetId, data }: { targetId: number; data: ProxyDeployRequest }) =>
      api.deployProxy(targetId, data),
    onSuccess: async (_result, variables) => {
      // Use refetchQueries so the fresh "deploying" status is in cache
      // before the polling refetchInterval evaluators run.
      await Promise.all([
        queryClient.refetchQueries({ queryKey: queryKeys.benchmarks.targets.proxies(variables.targetId) }),
        queryClient.refetchQueries({ queryKey: queryKeys.benchmarks.targets.detail(variables.targetId) }),
      ]);
      notify.success(`Deploying ${variables.data.proxy_type} proxy — Helm install in progress`, undefined, { category: 'deployment' });
    },
    onError: (error) => notifyError(error),
  });
};

export const useUpdateProxyDeployment = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ targetId, proxyId, data }: { targetId: number; proxyId: number; data: ProxyDeploymentUpdate }) =>
      api.updateProxyDeployment(targetId, proxyId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.proxies(variables.targetId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.detail(variables.targetId) });
    },
    onError: (error) => notifyError(error),
  });
};

export const useDeleteProxyDeployment = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ targetId, proxyId }: { targetId: number; proxyId: number }) =>
      api.deleteProxyDeployment(targetId, proxyId),
    onSuccess: async (_, variables) => {
      await Promise.all([
        queryClient.refetchQueries({ queryKey: queryKeys.benchmarks.targets.proxies(variables.targetId) }),
        queryClient.refetchQueries({ queryKey: queryKeys.benchmarks.targets.detail(variables.targetId) }),
      ]);
      notify.success('Proxy removal initiated', undefined, { category: 'deployment' });
    },
    onError: (error) => notifyError(error),
  });
};

export const useRedeployProxy = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ targetId, proxyId }: { targetId: number; proxyId: number }) =>
      api.redeployProxy(targetId, proxyId),
    onSuccess: async (_, variables) => {
      await Promise.all([
        queryClient.refetchQueries({ queryKey: queryKeys.benchmarks.targets.proxies(variables.targetId) }),
        queryClient.refetchQueries({ queryKey: queryKeys.benchmarks.targets.detail(variables.targetId) }),
      ]);
      notify.success('Proxy redeployment triggered', undefined, { category: 'deployment' });
    },
    onError: (error) => notifyError(error),
  });
};

// ============================================================================
// Proxy Discovery (Phase 5) — scan cluster for existing proxies
// ============================================================================

export const useDiscoverProxies = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (targetId: number) => api.discoverProxies(targetId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.proxies(result.target_id) });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.detail(result.target_id) });
      const found = result.discovered_count;
      const total = result.total_scanned;
      notify.success(`Discovered ${found} of ${total} proxy types on cluster`, undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

// ============================================================================
// Target Discovery (Phase 5b) — scan cluster for LLM services
// ============================================================================

export const useDiscoverTargets = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (data: DiscoverTargetsRequest) => api.discoverTargets(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.targets.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.configs.all });
      const svcs = result.discovered_count;
      const created = result.created_targets.length;
      const proxyTotal = result.proxy_results.reduce((sum, r) => sum + (r.discovered_proxies || 0), 0);
      const configs = result.created_configs?.length ?? 0;
      if (created > 0) {
        const parts = [`Created ${created} target(s)`, `${proxyTotal} proxy type(s)`];
        if (configs > 0) parts.push(`${configs} config(s)`);
        notify.success(parts.join(', '), undefined, { category: 'general' });
      } else if (svcs > 0 && !result.created_targets.length) {
        // Scan-only mode (no auto_create) — just show count
        notify.success(`Found ${svcs} LLM service(s) on cluster`, undefined, { category: 'general' });
      } else if (svcs === 0) {
        notify.info('No LLM services found on cluster', undefined, { category: 'general' });
      }
    },
    onError: (error) => notifyError(error),
  });
};

// ============================================================================
// Run Orchestration (Phase 4d)
// ============================================================================

export const useTriggerRun = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ targetId, proxyId, data }: { targetId: number; proxyId: number; data: TriggerRunRequest }) =>
      api.triggerRun(targetId, proxyId, data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.all });
      notify.success(result.message, undefined, { category: 'general' });
    },
    onError: (error) => notifyError(error),
  });
};

// ============================================================================
// Scenarios + Run-Groups
// ============================================================================

/** Terminal run-group statuses — polling stops once the group reaches one of these. */
const RUN_GROUP_TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled']);

export const useScenarios = () =>
  useQuery({
    queryKey: queryKeys.benchmarks.scenarios(),
    queryFn: () => api.getScenarios(),
  });

export const useRunScenario = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ targetId, proxyId, data }: { targetId: number; proxyId: number; data: ScenarioRunRequest }) =>
      api.runScenario(targetId, proxyId, data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runGroups.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runGroups.detail(result.run_group_id) });
      notify.success(result.message);
    },
    onError: (error) => notifyError(error),
  });
};

export const useRunGroup = (groupId: number | undefined) =>
  useQuery({
    queryKey: queryKeys.benchmarks.runGroups.detail(groupId!),
    queryFn: () => api.getRunGroup(groupId!),
    enabled: !!groupId,
    // Poll while the run-group is still expanding/running; stop once terminal.
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data && !RUN_GROUP_TERMINAL_STATES.has(data.status)) {
        return POLL_INTERVALS.FAST;
      }
      return false;
    },
  });

// ============================================================================
// WebSocket Hook for Real-time Progress
// ============================================================================

export const useBenchmarkWebSocket = (runId: number | undefined) => {
  const wsRef = useRef<WebSocket | null>(null);
  const [lastMessage, setLastMessage] = useState<BenchmarkWSMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const queryClient = useQueryClient();

  const connect = useCallback(() => {
    if (!runId) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = `${protocol}//${host}/ws/benchmarks/runs/${runId}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setIsConnected(true);

    ws.onmessage = (event) => {
      try {
        const message: BenchmarkWSMessage = JSON.parse(event.data);
        setLastMessage(message);

        if (message.type === 'run_completed' || message.type === 'run_failed') {
          queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.detail(runId) });
          queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runs.all });
          queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.summary() });
          // This run may be a child of a scenario run-group; refresh group aggregates.
          // The handler doesn't know the parent group id, so invalidate the whole
          // run-groups subtree (prefix-matches runGroups.detail(id)).
          queryClient.invalidateQueries({ queryKey: queryKeys.benchmarks.runGroups.all });
        }
      } catch {
        // Ignore non-JSON messages (e.g., "pong")
      }
    };

    ws.onclose = () => setIsConnected(false);

    const pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send('ping');
    }, 25000);

    return () => {
      clearInterval(pingInterval);
      ws.close();
    };
  }, [runId, queryClient]);

  useEffect(() => {
    const cleanup = connect();
    return cleanup;
  }, [connect]);

  return { lastMessage, isConnected };
};
