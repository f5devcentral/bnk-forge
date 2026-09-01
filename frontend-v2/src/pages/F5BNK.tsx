/**
 * F5 BNK (BIG-IP Next for Kubernetes) Page
 *
 * Thin orchestrator that wires together:
 * - f5bnk-parts/F5BNKSidebar — category navigation
 * - f5bnk-parts/F5BNKResourceTable — table + registry-driven action menus
 * - f5bnk-parts/F5BNKDetailPanel — detail panel with registry-driven component lookup
 * - f5bnk-parts/resource-registry — maps resource.kind → components/actions/icons
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ResourcePageHeader } from '@/components/layout/ResourcePageHeader';
import { ResourceExplorerLayout } from '@/components/layout/ResourceExplorerLayout';
import { ResourceViewTabs } from '@/components/layout/ResourceViewTabs';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Server, AlertTriangle, Activity, Plus, Globe, Shield, ShieldAlert,
  ChevronDown, List, FileText, Code, Download, Network,
} from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useProjectClusters, useClusterNamespaces } from '@/hooks/useK8s';
import { useBnkData } from '@/hooks/k8s/useBnk';
import { useAllClusters } from '@/hooks/useK8sClusters';
import { useProjects } from '@/hooks/useProjects';
import { parseApiError } from '@/lib/error-handler';
import { SkeletonTable } from '@/components/ui/skeleton-table';
import { EmptyState } from '@/components/ui/empty-state';
import { useDebounce } from '@/hooks/useDebounce';
import { useAutoSelectProjectCluster } from '@/hooks/useAutoSelectProjectCluster';
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts';
import { ResourceDescribeViewer } from '@/components/k8s/ResourceDescribeViewer';
import { ResourceDeleteDialog } from '@/components/k8s/ResourceDeleteDialog';
import { ResourceEditDialog } from '@/components/k8s/ResourceEditDialog';
import { ResourceCreateDialog } from '@/components/k8s/ResourceCreateDialog';
import { F5BNKPolicyViewer } from '@/components/k8s/F5BNKPolicyViewer';
import { F5AIAnalyzerViewer } from '@/components/k8s/F5AIAnalyzerViewer';
import { F5iRuleViewer } from '@/components/k8s/F5iRuleViewer';
import { F5BNKTopologyViewer } from '@/components/k8s/F5BNKTopologyViewer';
import { BackendsCollection } from '@/components/k8s/BackendsCollection';
import { PolicyBuilder } from '@/components/k8s/PolicyBuilder';
import { ConfigBuilder } from '@/components/k8s/ConfigBuilder';
import { BNKHealthDashboard } from '@/components/k8s/BNKHealthDashboard';
import { BNKUpgradePanel } from '@/components/k8s/BNKUpgradePanel';
import { BNKReleaseRegistry } from '@/components/k8s/BNKReleaseRegistry';
import { TrafficFlowOverview } from '@/components/k8s/TrafficFlowOverview';
import { RunbookWizard } from '@/components/runbooks/RunbookWizard';
import { QKViewPanel } from '@/components/k8s/QKViewPanel';
import { LicensingPanel } from '@/components/k8s/LicensingPanel';
import { TMMDebugPanel } from '@/components/k8s/TMMDebugPanel';
import { RecoveryPanel } from '@/components/k8s/RecoveryPanel';
import { A2AAgentDiscovery } from '@/components/k8s/A2AAgentDiscovery';
import { A2ATemplates } from '@/components/k8s/A2ATemplates';
import { A2AIRuleLibrary } from '@/components/k8s/A2AIRuleLibrary';
import { A2AProtocolReference } from '@/components/k8s/A2AProtocolReference';
// DPFInfrastructurePanel moved to Fleet page (/fleet?tab=dpf)
import { DEBOUNCE_MS } from '@/lib/constants';
import { notify } from '@/lib/notify';
import { STORAGE_KEYS } from '@/lib/storage-keys';
import { ConnectivityGate } from '@/components/ConnectivityGate';
import { useClusterReachable } from '@/hooks/useConnectivity';
import type { K8sResource } from '@/types/kubernetes';
import type { TopologyResourceSelection } from '@/components/k8s/F5BNKTopologyViewer';

import {
  VIEW_HEALTH, VIEW_POLICY_MAP, VIEW_AI_ANALYZERS, VIEW_TOPOLOGY, VIEW_TRAFFIC_FLOW, VIEW_UPGRADE, VIEW_DIAGNOSTICS, VIEW_BACKENDS, VIEW_POLICY_BUILDER, VIEW_CONFIG_BUILDER, VIEW_DPF_INFRA,
  VIEW_A2A_DISCOVERY, VIEW_A2A_TEMPLATES, VIEW_A2A_IRULE_LIBRARY, VIEW_A2A_REFERENCE,
  isSpecialView,
  F5BNKSidebar, F5BNKResourceTable, F5BNKDetailPanel,
} from './f5bnk-parts';
import { useCrds } from '@/hooks/useCrds';
import { buildBnkCategories, BNK_CRD_GROUPS } from './f5bnk-parts/bnk-categories';

// ---------------------------------------------------------------------------
// Diagnostics View — tabbed layout: Licensing + TMM Debug + QKView + Runbooks + Recovery
// ---------------------------------------------------------------------------

function DiagnosticsView({ clusterId, descClass }: { clusterId: number; descClass: string }) {
  const [activeTab, setActiveTab] = useState<'licensing' | 'tmm-debug' | 'qkview' | 'runbooks' | 'recovery'>('licensing');

  const tabBtn = (active: boolean) =>
    cn(
      'flex-1 px-4 py-2 rounded-md text-sm font-medium transition-colors',
      active
        ? 'bg-card text-foreground shadow-sm'
        : 'text-muted-foreground hover:text-foreground',
    );

  return (
    <div className="p-6 overflow-y-auto">
      <div className="max-w-5xl mx-auto">
        <div className="mb-6">
          <h2 className="text-xl font-semibold mb-2">Diagnostics</h2>
          <p className={descClass}>
            License diagnostics, TMM inspection, QKView collection, runbooks, and recovery actions for BNK
          </p>
          <p className="text-xs mt-2 text-muted-foreground">
            Next step: validate proxy behavior in{' '}
            <a href="/benchmarks" className="font-medium underline-offset-2 hover:underline text-primary">
              Performance Benchmarks
            </a>
            .
          </p>
        </div>

        {/* Tab Buttons */}
        <div className="flex gap-1 p-1 rounded-lg border border-border mb-6 bg-muted/50">
          <button onClick={() => setActiveTab('licensing')} className={tabBtn(activeTab === 'licensing')}>
            Licensing
          </button>
          <button onClick={() => setActiveTab('tmm-debug')} className={tabBtn(activeTab === 'tmm-debug')}>
            TMM Debug
          </button>
          <button onClick={() => setActiveTab('qkview')} className={tabBtn(activeTab === 'qkview')}>
            QKView
          </button>
          <button onClick={() => setActiveTab('runbooks')} className={tabBtn(activeTab === 'runbooks')}>
            Runbooks
          </button>
          <button onClick={() => setActiveTab('recovery')} className={tabBtn(activeTab === 'recovery')}>
            Recovery
          </button>
        </div>

        {/* Tab Content */}
        {activeTab === 'licensing' && <LicensingPanel clusterId={clusterId} />}
        {activeTab === 'tmm-debug' && <TMMDebugPanel clusterId={clusterId} />}
        {activeTab === 'qkview' && <QKViewPanel clusterId={clusterId} />}
        {activeTab === 'runbooks' && <RunbookWizard clusterId={clusterId} />}
        {activeTab === 'recovery' && <RecoveryPanel clusterId={clusterId} />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Special View Renderers
// ---------------------------------------------------------------------------

interface SpecialViewProps {
  clusterId: number;
  namespace: string | undefined;
  onTopologySelect?: (selection: TopologyResourceSelection) => void;
  onNavigateView?: (viewKey: string) => void;
  onRedirectToFleetDpf?: (clusterId: number) => void;
}

function renderSpecialView(viewType: string, { clusterId, namespace, onTopologySelect, onNavigateView, onRedirectToFleetDpf }: SpecialViewProps) {
  const descClass = 'text-sm text-muted-foreground';

  switch (viewType) {
    case VIEW_TRAFFIC_FLOW:
      return (
        <div className="p-6 overflow-y-auto">
          <div className="mb-6">
            <h2 className="text-xl font-semibold mb-2">Traffic Flow</h2>
            <p className={descClass}>How traffic flows through BNK — infrastructure, gateways, routes, and backends with configuration insights</p>
          </div>
          <TrafficFlowOverview clusterId={clusterId} namespace={namespace} onSelectResource={onTopologySelect} onNavigateView={onNavigateView} />
        </div>
      );

    case VIEW_HEALTH:
      return (
        <div className="p-6 overflow-y-auto">
          <div className="mb-6">
            <h2 className="text-xl font-semibold mb-2">BNK Health Dashboard</h2>
            <p className={descClass}>Real-time health status for all F5 BNK platform components — auto-refreshes every 30 seconds</p>
          </div>
          <BNKHealthDashboard clusterId={clusterId} namespace={namespace} />
        </div>
      );

    case VIEW_TOPOLOGY:
      return (
        <div className="p-6">
          <div className="mb-6">
            <h2 className="text-xl font-semibold mb-2">Gateway Topology</h2>
            <p className={descClass}>Complete object graph — click any resource name to view its details</p>
          </div>
          <F5BNKTopologyViewer clusterId={clusterId} namespace={namespace} onSelectResource={onTopologySelect} />
        </div>
      );

    case VIEW_POLICY_MAP:
      return (
        <div className="p-6">
          <div className="max-w-6xl mx-auto">
            <div className="mb-6">
              <h2 className="text-xl font-semibold mb-2">Policy Associations</h2>
              <p className={descClass}>View and manage F5 BNK security policies attached to Gateway listeners and egress traffic</p>
            </div>
            <F5BNKPolicyViewer clusterId={clusterId} namespace={namespace} onSelectResource={onTopologySelect} />
          </div>
        </div>
      );

    case VIEW_AI_ANALYZERS:
      return (
        <div className="p-6">
          <div className="max-w-6xl mx-auto">
            <div className="mb-6">
              <h2 className="text-xl font-semibold mb-2">AI Analyzers</h2>
              <p className={descClass}>Intelligent traffic distribution for LLM inference workloads using F5BigAnalyzer CRDs</p>
            </div>
            <F5AIAnalyzerViewer clusterId={clusterId} namespace={namespace} />
          </div>
        </div>
      );

    case VIEW_UPGRADE:
      return (
        <div className="p-6 overflow-y-auto">
          <div className="max-w-5xl mx-auto">
            <div className="mb-6">
              <h2 className="text-xl font-semibold mb-2">BNK Upgrade</h2>
              <p className={descClass}>Manage BNK platform upgrades with pre-flight checks, rolling execution, and rollback support</p>
            </div>
            <BNKUpgradePanel clusterId={clusterId} />
            <div className="mt-6">
              <BNKReleaseRegistry clusterId={clusterId} />
            </div>
          </div>
        </div>
      );

    case VIEW_BACKENDS:
      return (
        <div className="p-6 overflow-y-auto">
          <div className="mb-6">
            <h2 className="text-xl font-semibold mb-2">Backends</h2>
            <p className={descClass}>Services cross-referenced with route backends — see which are mapped vs available</p>
          </div>
          <BackendsCollection clusterId={clusterId} namespace={namespace} />
        </div>
      );

    case VIEW_POLICY_BUILDER:
      return (
        <div className="p-6 overflow-y-auto">
          <div className="mb-6">
            <h2 className="text-xl font-semibold mb-2">Policy Builder</h2>
            <p className={descClass}>Visually create and attach security and network policies to Gateway listeners</p>
          </div>
          <PolicyBuilder clusterId={clusterId} namespace={namespace} />
        </div>
      );

    case VIEW_CONFIG_BUILDER:
      return (
        <div className="p-6 overflow-y-auto">
          <div className="mb-6">
            <h2 className="text-xl font-semibold mb-2">Configuration Builder</h2>
            <p className={descClass}>Multi-step wizard to create complete Gateway configurations — listeners, routes, security, and network policies</p>
          </div>
          <ConfigBuilder clusterId={clusterId} namespace={namespace} />
        </div>
      );

    case VIEW_DPF_INFRA:
      // DPF moved to Fleet page — redirect immediately
      onRedirectToFleetDpf?.(clusterId);
      return null;

    case VIEW_DIAGNOSTICS:
      return (
        <DiagnosticsView clusterId={clusterId} descClass={descClass} />
      );

    // A2A Protocol views
    case VIEW_A2A_DISCOVERY:
      return (
        <div className="p-6 overflow-y-auto">
          <div className="mb-6">
            <h2 className="text-xl font-semibold mb-2">Agent Discovery</h2>
            <p className={descClass}>Discover A2A-capable agents running behind BNK Gateways — services behind HTTPRoutes that implement the A2A protocol</p>
          </div>
          <A2AAgentDiscovery clusterId={clusterId} namespace={namespace} />
        </div>
      );

    case VIEW_A2A_TEMPLATES:
      return (
        <div className="p-6 overflow-y-auto">
          <div className="mb-6">
            <h2 className="text-xl font-semibold mb-2">A2A Templates</h2>
            <p className={descClass}>Pre-built Gateway API and iRule configurations for A2A traffic patterns</p>
          </div>
          <A2ATemplates clusterId={clusterId} namespace={namespace} />
        </div>
      );

    case VIEW_A2A_IRULE_LIBRARY:
      return (
        <div className="p-6 overflow-y-auto">
          <div className="mb-6">
            <h2 className="text-xl font-semibold mb-2">A2A iRule Library</h2>
            <p className={descClass}>Curated iRules for A2A traffic — JSON-RPC inspection, session persistence, JWT validation</p>
          </div>
          <A2AIRuleLibrary clusterId={clusterId} namespace={namespace} />
        </div>
      );

    case VIEW_A2A_REFERENCE:
      return (
        <div className="p-6 overflow-y-auto">
          <div className="mb-6">
            <h2 className="text-xl font-semibold mb-2">A2A Protocol Reference</h2>
            <p className={descClass}>Quick reference for Google&apos;s Agent-to-Agent protocol — methods, task states, agent card schema</p>
          </div>
          <A2AProtocolReference clusterId={clusterId} namespace={namespace} />
        </div>
      );

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function F5BNK() {
  const borderDefault = 'border-border';
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const redirectToFleetDpf = useCallback((clusterId: number) => {
    navigate(`/fleet?tab=dpf&cluster=${clusterId}`, { replace: true });
  }, [navigate]);

  // Project and cluster selection (persisted to localStorage)
  const { data: projects } = useProjects();
  const { data: allClustersResponse } = useAllClusters();
  const allClusters = allClustersResponse?.clusters ?? [];

  const [selectedProject, setSelectedProject] = useState<number | null>(() => {
    const stored = localStorage.getItem(STORAGE_KEYS.BNK_PROJECT);
    return stored ? parseInt(stored) : null;
  });
  const [selectedCluster, setSelectedCluster] = useState<number | null>(() => {
    const stored = localStorage.getItem(STORAGE_KEYS.BNK_CLUSTER);
    return stored ? parseInt(stored) : null;
  });

  const { data: clusters = [] } = useProjectClusters(selectedProject ?? 0, {
    pollingEnabled: false,
  });

  // Auto-resolve project from cluster (e.g. navigating from Fleet with only cluster ID)
  useEffect(() => {
    if (selectedCluster && !selectedProject && allClusters.length > 0) {
      const match = allClusters.find((c) => c.id === selectedCluster);
      if (match?.project_id) {
        setSelectedProject(match.project_id);
      }
    }
  }, [selectedCluster, selectedProject, allClusters]);

  const visibleClusters = useMemo(() => {
    if (!selectedProject) return clusters ?? [];
    if ((clusters ?? []).length > 0) return clusters ?? [];
    return allClusters.filter((cluster) => cluster.project_id === selectedProject);
  }, [selectedProject, clusters, allClusters]);

  useAutoSelectProjectCluster({
    projects,
    allClusters,
    visibleClusters,
    selectedProject,
    selectedCluster,
    setSelectedProject,
    setSelectedCluster,
  });

  // Resource type & sidebar state
  const [selectedResourceType, setSelectedResourceType] = useState<string>(() => {
    const initialView = localStorage.getItem('bnk-forge-bnk-initial-view');
    if (initialView) {
      localStorage.removeItem('bnk-forge-bnk-initial-view');
      // DPF moved to Fleet — redirect if deep-link targets it
      if (initialView === VIEW_DPF_INFRA) {
        // Navigate after mount via useEffect (can't call navigate in useState initializer)
        setTimeout(() => navigate('/fleet?tab=dpf'), 0);
        return VIEW_HEALTH;
      }
      return initialView;
    }
    return VIEW_HEALTH;
  });
  const [searchQuery, setSearchQuery] = useState('');
  const debouncedSearch = useDebounce(searchQuery, DEBOUNCE_MS.SEARCH);
  const [selectedNamespace, setSelectedNamespace] = useState<string>('all');

  // Selected resource for detail panel
  const [selectedResource, setSelectedResource] = useState<K8sResource | null>(null);

  // Dialog state
  const [describeDialogOpen, setDescribeDialogOpen] = useState(false);
  const [resourceToDescribe, setResourceToDescribe] = useState<K8sResource | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [resourceToDelete, setResourceToDelete] = useState<K8sResource | null>(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [resourceToEdit, setResourceToEdit] = useState<K8sResource | null>(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [iRuleViewerOpen, setIRuleViewerOpen] = useState(false);
  const [iRuleResource, setIRuleResource] = useState<K8sResource | null>(null);

  // Topology-selected resource (shown as overlay detail panel in topology view)
  const [topologySelectedResource, setTopologySelectedResource] = useState<K8sResource | null>(null);
  const [topologyLoading, setTopologyLoading] = useState(false);

  // Search input ref for keyboard shortcut
  const searchInputRef = useRef<HTMLInputElement>(null);

  useKeyboardShortcuts([
    {
      key: '/',
      action: (e) => {
        e.preventDefault();
        searchInputRef.current?.focus();
      },
      allowInInput: true,
    },
  ]);

  // Persist selections to localStorage
  useEffect(() => {
    if (selectedProject !== null) {
      localStorage.setItem(STORAGE_KEYS.BNK_PROJECT, selectedProject.toString());
    } else {
      localStorage.removeItem(STORAGE_KEYS.BNK_PROJECT);
    }
  }, [selectedProject]);

  useEffect(() => {
    if (selectedCluster !== null) {
      localStorage.setItem(STORAGE_KEYS.BNK_CLUSTER, selectedCluster.toString());
    } else {
      localStorage.removeItem(STORAGE_KEYS.BNK_CLUSTER);
    }
  }, [selectedCluster]);

  // Clear cluster selection when project changes
  useEffect(() => {
    if (selectedProject && selectedCluster) {
      const clusterExistsInProject = visibleClusters.some(c => c.id === selectedCluster);
      if (!clusterExistsInProject && visibleClusters.length > 0) {
        setSelectedCluster(visibleClusters[0].id);
      } else if (!clusterExistsInProject) {
        setSelectedCluster(null);
      }
    }
  }, [selectedProject, visibleClusters, selectedCluster]);

  // Defense-in-depth: don't fire K8s queries for an unreachable cluster.
  // The wrapping ConnectivityGate already swaps the UI for an offline banner,
  // but the queries below would still try to run if not gated here.
  const clusterReachable = useClusterReachable(selectedCluster ?? undefined);

  // CRD discovery — drives dynamic category merging (#140)
  const { data: crdsData } = useCrds(selectedCluster ?? 0, {
    enabled: !!selectedCluster && clusterReachable,
    group: BNK_CRD_GROUPS,
  });
  const discoveredCategories = useMemo(
    () => buildBnkCategories(crdsData?.crds ?? []),
    [crdsData?.crds],
  );

  // Fetch resources
  const { data: resources, isLoading: resourcesLoading, error: resourcesError, isFetching: isRefreshing } = useQuery({
    queryKey: ['bnk-resources', selectedCluster, selectedResourceType, selectedNamespace],
    queryFn: () => api.getClusterResources(selectedCluster!, selectedResourceType, {
       namespace: selectedNamespace === 'all' ? undefined : selectedNamespace
    }),
    enabled: !!selectedCluster && !!selectedResourceType && !isSpecialView(selectedResourceType) && clusterReachable,
    staleTime: 30000,
  });

  const { data: namespacesResponse } = useClusterNamespaces(selectedCluster || 0, {
    enabled: !!selectedCluster && clusterReachable,
  });
  const namespaces = namespacesResponse?.namespaces || [];

  // Filter resources by search
  const filteredResources: K8sResource[] = (resources?.resources?.filter((resource: K8sResource) => {
    return !debouncedSearch ||
      resource.metadata?.name?.toLowerCase().includes(debouncedSearch.toLowerCase());
  }) ?? []) as K8sResource[];

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleSelectResourceType = (type: string) => {
    setSelectedResourceType(type);
    setSelectedResource(null);
    setTopologySelectedResource(null);
  };

  // The active top-level category drives the header tabs + the (flat) sidebar.
  // Derived from the current selection so deep-links / programmatic nav keep
  // the right tab lit without a separate state to sync.
  const activeCategory = useMemo(() => {
    const found = discoveredCategories.find((c) =>
      c.items.some((i) => i.key === selectedResourceType),
    );
    return found?.category ?? discoveredCategories[0].category;
  }, [selectedResourceType, discoveredCategories]);

  const handleSelectCategory = (category: string) => {
    const cat = discoveredCategories.find((c) => c.category === category);
    if (cat && cat.items.length > 0) {
      handleSelectResourceType(cat.items[0].key);
    }
  };

  const handleRefresh = () => {
    if (!selectedCluster) return;
    // Invalidate ALL queries related to this cluster:
    //   - 'bnk-resources' → resource list views (gateways, routes, policies, etc.)
    //   - 'k8s/clusters/N' → special views (health, topology, policy-map, upgrade, etc.)
    //   - 'runbooks' → diagnostics runbook definitions
    //   - 'tmm-debug' → TMM debug pod discovery
    //   - 'qkview' → QKView operations
    //   - 'licensing' → BNK licensing status
    queryClient.invalidateQueries({ queryKey: ['bnk-resources'] });
    queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', selectedCluster] });
    queryClient.invalidateQueries({ queryKey: ['runbooks'] });
    queryClient.invalidateQueries({ queryKey: ['tmm-debug'] });
    queryClient.invalidateQueries({ queryKey: ['qkview'] });
    queryClient.invalidateQueries({ queryKey: ['licensing'] });
  };

  const handleDescribe = (resource: K8sResource) => {
    setResourceToDescribe(resource);
    setDescribeDialogOpen(true);
  };

  const handleEdit = (resource: K8sResource) => {
    setResourceToEdit(resource);
    setEditDialogOpen(true);
  };

  const handleDelete = (resource: K8sResource) => {
    setResourceToDelete(resource);
    setDeleteDialogOpen(true);
  };

  const handleNavigateView = (viewKey: string) => {
    setSelectedResourceType(viewKey);
    setSelectedResource(null);
  };

  const handleOpenDialog = (dialogKey: string, resource: K8sResource) => {
    if (dialogKey === 'irule-viewer') {
      setIRuleResource(resource);
      setIRuleViewerOpen(true);
    }
  };

  // Handle topology resource selection — fetch full resource and show detail panel
  const handleTopologySelect = async (selection: TopologyResourceSelection) => {
    if (!selectedCluster) return;
    setTopologyLoading(true);
    try {
      // Map kind to the API resource type key (lowercase)
      const resourceType = selection.kind.toLowerCase();
      const result = await api.getClusterResources(selectedCluster, resourceType, {
        namespace: selection.namespace,
      });
      const match = result.resources?.find(
        (r: K8sResource) => r.metadata?.name === selection.name
      );
      if (match) {
        setTopologySelectedResource(match);
      } else {
        notify.error('Resource not found', `Could not find ${selection.kind} "${selection.name}" in namespace "${selection.namespace}"`, { category: 'cluster' });
      }
    } catch (error: unknown) {
      const parsed = parseApiError(error);
      notify.error('Failed to load resource', parsed.message, { category: 'cluster' });
    } finally {
      setTopologyLoading(false);
    }
  };

  // Export config
  const [isExporting, setIsExporting] = useState(false);

  const handleExportConfig = async (format: 'yaml' | 'json') => {
    if (!selectedCluster) return;
    setIsExporting(true);
    try {
      if (format === 'yaml') {
        const blob = await api.exportClusterConfig(selectedCluster);
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const cluster = clusters.find(c => c.id === selectedCluster);
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        a.download = `bnk-config-${cluster?.name || selectedCluster}-${timestamp}.yaml`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        notify.success('BNK configuration exported as YAML', undefined, { category: 'cluster' });
      } else {
        const config = await api.exportClusterConfigJson(selectedCluster);
        const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const cluster = clusters.find(c => c.id === selectedCluster);
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        a.download = `bnk-config-${cluster?.name || selectedCluster}-${timestamp}.json`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        notify.success('BNK configuration exported as JSON', undefined, { category: 'cluster' });
      }
    } catch (error: unknown) {
      const parsed = parseApiError(error);
      notify.error('Export failed', parsed.message, { category: 'cluster' });
    } finally {
      setIsExporting(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Resource list view content (non-special views)
  // ---------------------------------------------------------------------------

  const renderResourceListContent = () => (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Toolbar */}
      <div className={cn('flex items-center justify-between px-4 py-2 border-b', borderDefault)}>
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">
            {discoveredCategories.find(c => c.items.find(i => i.key === selectedResourceType))?.items.find(i => i.key === selectedResourceType)?.label || selectedResourceType}
            <span className="text-muted-foreground"> ({filteredResources.length})</span>
          </h2>
        </div>
        <Button
          size="sm"
          className="ml-3"
          onClick={() => setCreateDialogOpen(true)}
          disabled={!selectedCluster}
        >
          <Plus className="h-4 w-4 mr-2" />
          Create
        </Button>
      </div>

      {/* Resource Table */}
      <div className="flex-1 overflow-auto">
        {resourcesLoading ? (
          <div className="p-4">
            <SkeletonTable rows={5} columns={5} />
          </div>
        ) : resourcesError ? (
          <div className="p-4">
            <EmptyState
              icon={AlertTriangle}
              title="Failed to load resources"
              description={parseApiError(resourcesError).message}
            />
          </div>
        ) : filteredResources.length === 0 ? (
          <div className="p-4">
            {renderContextEmptyState()}
          </div>
        ) : (
          <F5BNKResourceTable
            resources={filteredResources}
            selectedResource={selectedResource}
            onSelectResource={setSelectedResource}
            onDescribe={handleDescribe}
            onEdit={handleEdit}
            onDelete={handleDelete}
            onNavigateView={handleNavigateView}
            onOpenDialog={handleOpenDialog}
            borderDefault={borderDefault}
          />
        )}
      </div>
    </div>
  );

  /** Context-aware empty states for specific resource types */
  const renderContextEmptyState = () => {
    if (!searchQuery && selectedResourceType === 'f5bigfwrulelist') {
      return (
        <div className="max-w-lg mx-auto text-center py-12">
          <List className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
          <h3 className="text-lg font-semibold mb-2 text-foreground">
            No standalone rule lists
          </h3>
          <p className="text-sm mb-4 text-muted-foreground">
            Firewall rules are defined inline within Firewall Policies. View them there, or create a standalone rule list to share rules across multiple policies.
          </p>
          <div className="flex justify-center gap-2">
            <Button variant="outline" size="sm" onClick={() => handleSelectResourceType('f5bigfwpolicy')}>
              <Shield className="h-4 w-4 mr-2" />
              View Firewall Policies
            </Button>
            <Button size="sm" onClick={() => setCreateDialogOpen(true)}>
              <Plus className="h-4 w-4 mr-2" />
              Create Rule List
            </Button>
          </div>
        </div>
      );
    }

    // If the backend returned an info message (e.g. CRD not installed), show it
    if (!searchQuery && resources?.info) {
      return (
        <div className="max-w-lg mx-auto text-center py-12">
          <AlertTriangle className="h-10 w-10 mx-auto mb-3 text-warning" />
          <h3 className="text-lg font-semibold mb-2 text-foreground">
            Resource type not available
          </h3>
          <p className="text-sm text-muted-foreground">
            {resources.info}
          </p>
        </div>
      );
    }

    if (!searchQuery && (selectedResourceType === 'f5bigcneaddresslist' || selectedResourceType === 'f5bigcneportlist')) {
      const isAddr = selectedResourceType === 'f5bigcneaddresslist';
      return (
        <div className="max-w-lg mx-auto text-center py-12">
          <Network className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
          <h3 className="text-lg font-semibold mb-2 text-foreground">
            No {isAddr ? 'address lists' : 'port lists'} found
          </h3>
          <p className="text-sm mb-4 text-muted-foreground">
            {isAddr
              ? 'Address lists define IP addresses and CIDR ranges used by firewall policies. Create one to reference in your Firewall Policies.'
              : 'Port lists define port ranges used by firewall policies. Create one to reference in your Firewall Policies.'}
          </p>
          <div className="flex justify-center gap-2">
            <Button variant="outline" size="sm" onClick={() => handleSelectResourceType('f5bigfwpolicy')}>
              <Shield className="h-4 w-4 mr-2" />
              View Firewall Policies
            </Button>
            <Button size="sm" onClick={() => setCreateDialogOpen(true)}>
              <Plus className="h-4 w-4 mr-2" />
              Create {isAddr ? 'Address List' : 'Port List'}
            </Button>
          </div>
        </div>
      );
    }

    if (!searchQuery && selectedResourceType === 'f5bigddosglobal') {
      return (
        <div className="max-w-lg mx-auto text-center py-12">
          <ShieldAlert className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
          <h3 className="text-lg font-semibold mb-2 text-foreground">
            DDoS protection not configured
          </h3>
          <p className="text-sm mb-4 text-muted-foreground">
            Create a DDoS Global configuration to enable network-level flood protection with auto-threshold detection and mitigation.
          </p>
          <Button size="sm" onClick={() => setCreateDialogOpen(true)}>
            <Plus className="h-4 w-4 mr-2" />
            Configure DDoS Protection
          </Button>
        </div>
      );
    }

    return (
      <EmptyState
        icon={Globe}
        title={searchQuery ? `No resources match "${searchQuery}"` : `No ${selectedResourceType} resources found`}
        description="Try selecting a different resource type or namespace"
      />
    );
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const resolvedNamespace = selectedNamespace === 'all' ? undefined : selectedNamespace;

  // Prefetch the unified BNK data bundle in the background while the user is
  // on any BNK tab. This warms the cache for Traffic Flow / Topology / Policy
  // so tab switching feels instant; the lightweight /f5bnk/health endpoint
  // still drives the Health Dashboard landing view.
  useBnkData(
    selectedCluster ?? 0,
    { namespace: resolvedNamespace },
    { enabled: !!selectedCluster, pollingEnabled: false }
  );

  return (
    <ResourceExplorerLayout>
      {/* Header */}
      <ResourcePageHeader
        title="F5 BNK"
        subtitle="BIG-IP Next for Kubernetes — gateways, policies, and traffic flow"
        projects={projects || []}
        selectedProjectId={selectedProject}
        onProjectChange={(id) => {
          setSelectedProject(id);
          setSelectedCluster(null);
        }}
        clusters={visibleClusters}
        selectedClusterId={selectedCluster}
        onClusterChange={setSelectedCluster}
        namespaces={namespaces}
        selectedNamespace={selectedNamespace}
        onNamespaceChange={setSelectedNamespace}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        onRefresh={handleRefresh}
        isRefreshing={isRefreshing}
      >
        {selectedCluster && !isSpecialView(selectedResourceType) && (
          <Badge variant="outline" className="text-xs h-7">
            <Activity className="h-3 w-3 mr-1" />
            {filteredResources.length} resources
          </Badge>
        )}
        {selectedCluster && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs gap-1.5"
                disabled={isExporting}
              >
                <Download className={cn('h-3.5 w-3.5', isExporting && 'animate-pulse')} />
                Export Config
                <ChevronDown className="h-3 w-3 opacity-50" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => handleExportConfig('yaml')}>
                <FileText className="h-4 w-4 mr-2" />
                Export as YAML
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleExportConfig('json')}>
                <Code className="h-4 w-4 mr-2" />
                Export as JSON
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </ResourcePageHeader>

      {/* Category tab strip — one tab per top-level category (D-020). Shared
          ResourceViewTabs idiom, identical to the Kubernetes/CNF strips. */}
      {selectedCluster && (
        <ResourceViewTabs
          aria-label="F5 BNK category"
          active={activeCategory}
          onChange={handleSelectCategory}
          tabs={discoveredCategories.map((c) => ({
            key: c.category,
            label: c.category,
            icon: c.icon,
          }))}
        />
      )}

      {/* Main Content */}
      <ResourceExplorerLayout.Body>
        {/* Sidebar — items of the active category */}
        <F5BNKSidebar
          activeCategory={activeCategory}
          selectedResourceType={selectedResourceType}
          onSelectResourceType={handleSelectResourceType}
          categories={discoveredCategories}
        />

        {/* Center + Detail Panel */}
        {!selectedProject ? (
          <ResourceExplorerLayout.Content className="flex items-center justify-center">
            <EmptyState icon={Server} title="No project selected" description="Select a project to view its clusters and F5 BNK resources" />
          </ResourceExplorerLayout.Content>
        ) : !selectedCluster ? (
          <ResourceExplorerLayout.Content className="flex items-center justify-center">
            <EmptyState icon={Server} title="No cluster selected" description="Select a cluster to view F5 BNK resources" />
          </ResourceExplorerLayout.Content>
        ) : (
          <>
            {/* Center: Dynamic Content */}
            <ResourceExplorerLayout.Content className={cn('flex flex-col', isSpecialView(selectedResourceType) ? 'overflow-y-auto' : 'overflow-hidden')}>
              {/* Gate the entire BNK view on cluster reachability — every panel
                  here ultimately reads from the K8s API, so an offline cluster
                  means infinite spinners without this. */}
              <ConnectivityGate
                target={{
                  type: 'cluster',
                  id: selectedCluster,
                  displayName: allClusters.find((c) => c.id === selectedCluster)?.name,
                }}
              >
                {isSpecialView(selectedResourceType)
                  ? renderSpecialView(selectedResourceType, { clusterId: selectedCluster, namespace: resolvedNamespace, onTopologySelect: handleTopologySelect, onNavigateView: handleNavigateView, onRedirectToFleetDpf: redirectToFleetDpf })
                  : renderResourceListContent()
                }
              </ConnectivityGate>
            </ResourceExplorerLayout.Content>

            {/* Right Panel: Resource Details (resource list views + topology click) */}
            <ResourceExplorerLayout.DetailPanel
              open={
                (!!selectedResource && !isSpecialView(selectedResourceType)) ||
                (!!topologySelectedResource && (selectedResourceType === VIEW_TOPOLOGY || selectedResourceType === VIEW_TRAFFIC_FLOW || selectedResourceType === VIEW_POLICY_MAP))
              }
            >
              {/* Resource list selection */}
              {selectedResource && !isSpecialView(selectedResourceType) && (
                <F5BNKDetailPanel
                  resource={selectedResource}
                  onClose={() => setSelectedResource(null)}
                  onDescribe={handleDescribe}
                  onEdit={handleEdit}
                  onDelete={handleDelete}
                  onNavigateView={handleNavigateView}
                  onOpenDialog={handleOpenDialog}
                  borderDefault={borderDefault}
                />
              )}
              {/* Topology / Traffic Flow selection */}
              {topologySelectedResource && (selectedResourceType === VIEW_TOPOLOGY || selectedResourceType === VIEW_TRAFFIC_FLOW || selectedResourceType === VIEW_POLICY_MAP) && (
                <F5BNKDetailPanel
                  resource={topologySelectedResource}
                  onClose={() => setTopologySelectedResource(null)}
                  onDescribe={handleDescribe}
                  onEdit={handleEdit}
                  onDelete={handleDelete}
                  onNavigateView={handleNavigateView}
                  onOpenDialog={handleOpenDialog}
                  borderDefault={borderDefault}
                />
              )}
              {topologyLoading && (selectedResourceType === VIEW_TOPOLOGY || selectedResourceType === VIEW_TRAFFIC_FLOW || selectedResourceType === VIEW_POLICY_MAP) && (
                <div className="flex items-center justify-center py-20">
                  <Activity className="h-5 w-5 animate-spin text-primary mr-2" />
                  <span className="text-sm text-muted-foreground">
                    Loading resource…
                  </span>
                </div>
              )}
            </ResourceExplorerLayout.DetailPanel>
          </>
        )}
      </ResourceExplorerLayout.Body>

      {/* Dialogs */}
      {selectedCluster && (
        <>
          <ResourceDescribeViewer
            open={describeDialogOpen}
            onOpenChange={setDescribeDialogOpen}
            resource={resourceToDescribe}
            clusterId={selectedCluster}
            namespace={resourceToDescribe?.metadata?.namespace}
          />

          <ResourceDeleteDialog
            open={deleteDialogOpen}
            onOpenChange={setDeleteDialogOpen}
            resource={resourceToDelete}
            onConfirm={async () => {
              if (resourceToDelete && selectedCluster) {
                try {
                  await api.deleteK8sResource(
                    selectedCluster,
                    resourceToDelete.kind.toLowerCase(),
                    resourceToDelete.metadata.name,
                    { namespace: resourceToDelete.metadata.namespace }
                  );
                  notify.success(`${resourceToDelete.kind} deleted successfully`, undefined, { category: 'cluster' });
                  setDeleteDialogOpen(false);
                  setResourceToDelete(null);
                  setSelectedResource(null);
                  queryClient.invalidateQueries({ queryKey: ['bnk-resources'] });
                } catch (error: unknown) {
                  const parsed = parseApiError(error);
                  notify.error(parsed.title, parsed.message, { category: 'cluster' });
                }
              }
            }}
          />

          {resourceToEdit && (
            <ResourceEditDialog
              open={editDialogOpen}
              onOpenChange={setEditDialogOpen}
              resource={resourceToEdit}
              onSubmit={async (resourceYaml, dryRun) => {
                try {
                  await api.updateK8sResource(
                    selectedCluster,
                    resourceToEdit.kind.toLowerCase(),
                    resourceToEdit.metadata.name,
                    {
                      resource_yaml: resourceYaml,
                      namespace: resourceToEdit.metadata.namespace,
                      dry_run: dryRun
                    }
                  );
                  if (dryRun) {
                    notify.success('Dry run successful - no changes applied', undefined, { category: 'cluster' });
                  } else {
                    setEditDialogOpen(false);
                    setResourceToEdit(null);
                    queryClient.invalidateQueries({ queryKey: ['bnk-resources'] });
                  }
                } catch (error: unknown) {
                  const parsed = parseApiError(error);
                  notify.error(parsed.title, parsed.message, { category: 'cluster' });
                }
              }}
            />
          )}

          {iRuleResource && (
            <F5iRuleViewer
              open={iRuleViewerOpen}
              onOpenChange={setIRuleViewerOpen}
              resource={iRuleResource!}
            />
          )}

          <ResourceCreateDialog
            open={createDialogOpen}
            onOpenChange={setCreateDialogOpen}
            resourceType={selectedResourceType}
            namespace={selectedNamespace === 'all' ? 'default' : selectedNamespace}
            onSubmit={async (resourceYaml, dryRun) => {
              try {
                await api.createK8sResource(
                  selectedCluster,
                  selectedResourceType,
                  {
                    resource_yaml: resourceYaml,
                    namespace: selectedNamespace === 'all' ? 'default' : selectedNamespace,
                    dry_run: dryRun
                  }
                );
                if (dryRun) {
                  notify.success('Dry run successful - no changes applied', undefined, { category: 'cluster' });
                } else {
                  setCreateDialogOpen(false);
                  queryClient.invalidateQueries({ queryKey: ['bnk-resources'] });
                }
              } catch (error: unknown) {
                const parsed = parseApiError(error);
                notify.error(parsed.title, parsed.message, { category: 'cluster' });
              }
            }}
          />
        </>
      )}
    </ResourceExplorerLayout>
  );
}
