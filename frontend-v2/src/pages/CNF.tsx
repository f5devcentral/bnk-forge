/**
 * CNF Dashboard — Discovery-driven Custom Resource browser.
 *
 * Thin orchestrator that wires together:
 * - cnf-parts/CNFSidebar — runtime-built category navigation from /crds
 * - cnf-parts/CNFResourceTable — instance list with condition-derived status
 * - cnf-parts/CNFDetailPanel — read-only metadata + conditions + raw YAML
 *
 * READ-ONLY: no create/edit/delete/apply/patch controls anywhere.
 * Cluster/namespace selection is persisted to localStorage under CNF-specific keys.
 */

import { useState, useEffect, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { cn } from '@/lib/utils';
import { queryKeys } from '@/lib/queryKeys';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Activity, AlertTriangle, Globe, LayoutGrid, Network, Server, ExternalLink, ChevronLeft } from 'lucide-react';
import { ResourcePageHeader } from '@/components/layout/ResourcePageHeader';
import { ResourceExplorerLayout } from '@/components/layout/ResourceExplorerLayout';
import { ResourceViewTabs } from '@/components/layout/ResourceViewTabs';
import { EmptyState } from '@/components/ui/empty-state';
import { SkeletonTable } from '@/components/ui/skeleton-table';
import { ConnectivityGate } from '@/components/ConnectivityGate';
import { ResourceDescribeViewer } from '@/components/k8s/ResourceDescribeViewer';
import { ResourceTopologyGraph } from '@/components/k8s/ResourceTopologyGraph';
import { useProjects } from '@/hooks/useProjects';
import { useAllClusters } from '@/hooks/useK8sClusters';
import { useProjectClusters, useClusterNamespaces } from '@/hooks/useK8s';
import { useAutoSelectProjectCluster } from '@/hooks/useAutoSelectProjectCluster';
import { useClusterReachable } from '@/hooks/useConnectivity';
import { useClusterResources } from '@/hooks/useK8sResources';
import { useCrds } from '@/hooks/useCrds';
import { useTopology } from '@/hooks/useTopology';
import { parseApiError } from '@/lib/error-handler';
import { STORAGE_KEYS } from '@/lib/storage-keys';
import type { K8sResource } from '@/types/kubernetes';

import {
  buildCnfCategories,
  CNFSidebar,
  CNFResourceTable,
  CNFDetailPanel,
  type CRDInfo,
} from './cnf-parts';

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function CNF() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const borderDefault = 'border-border';

  // Project and cluster selection (persisted to localStorage)
  const { data: projects } = useProjects();
  const { data: allClustersResponse } = useAllClusters();
  const allClusters = allClustersResponse?.clusters ?? [];

  const [selectedProject, setSelectedProject] = useState<number | null>(() => {
    const stored = localStorage.getItem(STORAGE_KEYS.CNF_PROJECT);
    return stored ? parseInt(stored) : null;
  });
  const [selectedCluster, setSelectedCluster] = useState<number | null>(() => {
    const stored = localStorage.getItem(STORAGE_KEYS.CNF_CLUSTER);
    return stored ? parseInt(stored) : null;
  });

  const { data: clusters = [] } = useProjectClusters(selectedProject ?? 0, {
    pollingEnabled: false,
  });

  // Auto-resolve project from cluster (e.g. deep-link with cluster ID)
  useEffect(() => {
    if (selectedCluster && !selectedProject && allClusters.length > 0) {
      const match = allClusters.find((c) => c.id === selectedCluster);
      if (match?.project_id) setSelectedProject(match.project_id);
    }
  }, [selectedCluster, selectedProject, allClusters]);

  const visibleClusters = useMemo(() => {
    if (!selectedProject) return clusters ?? [];
    if ((clusters ?? []).length > 0) return clusters ?? [];
    return allClusters.filter((c) => c.project_id === selectedProject);
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

  // Namespace selector and search
  const [selectedNamespace, setSelectedNamespace] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');

  // View toggle: resource browser vs topology graph
  const [view, setView] = useState<'browser' | 'topology'>('browser');

  const clusterReachable = useClusterReachable(selectedCluster ?? undefined);

  // Persist selections
  useEffect(() => {
    if (selectedProject !== null) {
      localStorage.setItem(STORAGE_KEYS.CNF_PROJECT, selectedProject.toString());
    } else {
      localStorage.removeItem(STORAGE_KEYS.CNF_PROJECT);
    }
  }, [selectedProject]);

  useEffect(() => {
    if (selectedCluster !== null) {
      localStorage.setItem(STORAGE_KEYS.CNF_CLUSTER, selectedCluster.toString());
    } else {
      localStorage.removeItem(STORAGE_KEYS.CNF_CLUSTER);
    }
  }, [selectedCluster]);

  // Clear cluster when project changes and cluster no longer belongs to it
  useEffect(() => {
    if (selectedProject && selectedCluster) {
      const exists = visibleClusters.some((c) => c.id === selectedCluster);
      if (!exists && visibleClusters.length > 0) setSelectedCluster(visibleClusters[0].id);
      else if (!exists) setSelectedCluster(null);
    }
  }, [selectedProject, visibleClusters, selectedCluster]);

  // CRD discovery
  const { data: crdsData, isLoading: crdsLoading, error: crdsError } = useCrds(
    selectedCluster ?? 0,
    { enabled: !!selectedCluster && clusterReachable }
  );

  // Sidebar state
  const [selectedCrdKey, setSelectedCrdKey] = useState<string | null>(null);
  const [expandedCategories, setExpandedCategories] = useState<string[]>([]);

  // Build categories from discovery response
  const categories = useMemo(
    () => buildCnfCategories(crdsData?.crds ?? []),
    [crdsData?.crds]
  );

  // Auto-expand first category when categories load
  useEffect(() => {
    if (categories.length > 0 && expandedCategories.length === 0) {
      setExpandedCategories([categories[0].category]);
    }
  }, [categories, expandedCategories.length]);

  // Find the selected CRD's info
  const selectedCrdInfo: CRDInfo | null = useMemo(() => {
    if (!selectedCrdKey || !crdsData?.crds) return null;
    return crdsData.crds.find((c) => c.name === selectedCrdKey) ?? null;
  }, [selectedCrdKey, crdsData?.crds]);

  const handleSelectCrd = useCallback((key: string) => {
    setSelectedCrdKey(key);
    setSelectedResource(null);
  }, []);

  const toggleCategory = useCallback((category: string) => {
    setExpandedCategories((prev) =>
      prev.includes(category) ? prev.filter((c) => c !== category) : [...prev, category]
    );
  }, []);

  // Resource list for selected CRD
  const namespace = selectedNamespace === 'all' ? undefined : selectedNamespace;
  const { data: resourcesData, isLoading: resourcesLoading, error: resourcesError } = useClusterResources(
    selectedCluster ?? 0,
    selectedCrdKey ?? '',
    namespace ? { namespace } : undefined,
    { enabled: !!selectedCluster && !!selectedCrdKey && clusterReachable }
  );

  const resources: K8sResource[] = (resourcesData?.resources ?? []) as K8sResource[];

  // Namespaces for selector
  const { data: namespacesResponse } = useClusterNamespaces(selectedCluster ?? 0, {
    enabled: !!selectedCluster && clusterReachable,
  });
  const namespaces = namespacesResponse?.namespaces ?? [];

  // Topology graph (only when topology view is active + a real namespace is selected)
  const topologyNamespace = selectedNamespace === 'all' ? '' : selectedNamespace;
  const {
    data: topologyData,
    isLoading: topologyLoading,
    error: topologyError,
  } = useTopology(selectedCluster ?? 0, topologyNamespace, {
    enabled: view === 'topology' && !!selectedCluster && clusterReachable,
  });

  // Selected resource for detail panel
  const [selectedResource, setSelectedResource] = useState<K8sResource | null>(null);

  // Describe dialog
  const [describeOpen, setDescribeOpen] = useState(false);
  const [resourceToDescribe, setResourceToDescribe] = useState<K8sResource | null>(null);

  const handleDescribe = (resource: K8sResource) => {
    setResourceToDescribe(resource);
    setDescribeOpen(true);
  };

  // Refresh — invalidate the three cluster-scoped caches used by this page
  const handleRefresh = useCallback(() => {
    if (!selectedCluster) return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.crds(selectedCluster) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.allResources(selectedCluster) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.namespaces(selectedCluster) });
  }, [queryClient, selectedCluster]);

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  const renderCenterContent = () => {
    if (crdsLoading) {
      return (
        <div className="p-4">
          <SkeletonTable rows={5} columns={4} />
        </div>
      );
    }

    if (crdsError) {
      return (
        <div className="p-4">
          <EmptyState
            icon={AlertTriangle}
            title="Failed to load CRDs"
            description={parseApiError(crdsError).message}
          />
        </div>
      );
    }

    if (!crdsData || crdsData.crds.length === 0) {
      return (
        <div className="p-4">
          <EmptyState
            icon={Globe}
            title="No custom resource definitions found"
            description={crdsData?.info ?? 'No CRDs are installed on this cluster.'}
          />
        </div>
      );
    }

    if (!selectedCrdKey) {
      const enrichedCategories = categories.filter((c) => c.enriched);

      const filteredCategories = categories
        .map((cat) => ({
          ...cat,
          items: searchQuery.trim()
            ? cat.items.filter(
                (i) =>
                  i.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
                  i.key.toLowerCase().includes(searchQuery.toLowerCase()) ||
                  cat.category.toLowerCase().includes(searchQuery.toLowerCase())
              )
            : cat.items,
        }))
        .filter((cat) => cat.items.length > 0);

      return (
        <div className="p-6 space-y-6 overflow-y-auto max-w-6xl">
          {/* Top KPI Metrics Strip */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="p-3 rounded-lg border border-border bg-card shadow-xs">
              <div className="text-xs text-muted-foreground font-medium flex items-center gap-1.5">
                <Activity className="h-3.5 w-3.5 text-primary" />
                Discovered CRDs
              </div>
              <div className="text-xl font-bold text-foreground mt-1">{crdsData.count}</div>
              <div className="text-xs text-muted-foreground mt-0.5">Across all API groups</div>
            </div>

            <div className="p-3 rounded-lg border border-border bg-card shadow-xs">
              <div className="text-xs text-muted-foreground font-medium flex items-center gap-1.5">
                <LayoutGrid className="h-3.5 w-3.5 text-primary" />
                Categories
              </div>
              <div className="text-xl font-bold text-foreground mt-1">{categories.length}</div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {enrichedCategories.length} curated domains
              </div>
            </div>

            <div className="p-3 rounded-lg border border-border bg-card shadow-xs">
              <div className="text-xs text-muted-foreground font-medium flex items-center gap-1.5">
                <Server className="h-3.5 w-3.5 text-primary" />
                Active Cluster
              </div>
              <div className="text-sm font-semibold text-foreground mt-1.5 truncate">
                {visibleClusters.find((c) => c.id === selectedCluster)?.name ?? 'Selected Cluster'}
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {namespaces.length} namespaces
              </div>
            </div>

            <div className="p-3 rounded-lg border border-border bg-card shadow-xs">
              <div className="text-xs text-muted-foreground font-medium flex items-center gap-1.5">
                <Network className="h-3.5 w-3.5 text-primary" />
                Selected Scope
              </div>
              <div className="text-sm font-semibold text-foreground mt-1.5 truncate">
                {selectedNamespace === 'all' ? 'All Namespaces' : selectedNamespace}
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">Filter active</div>
            </div>
          </div>

          {/* CRD Category Cards Grid */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <LayoutGrid className="h-4 w-4 text-primary" />
                Resource Catalog
              </h3>
              <span className="text-xs text-muted-foreground">
                Click any resource definition to inspect instances and telemetry
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {filteredCategories.map((cat) => (
                <div
                  key={cat.category}
                  className="rounded-lg border border-border bg-card p-4 space-y-3 shadow-xs hover:border-primary/40 transition-colors"
                >
                  <div className="flex items-center justify-between border-b border-border/60 pb-2">
                    <div className="font-semibold text-xs text-foreground flex items-center gap-1.5">
                      {cat.category}
                    </div>
                    <Badge variant={cat.enriched ? 'default' : 'outline'} className="text-xs">
                      {cat.items.length} {cat.items.length === 1 ? 'type' : 'types'}
                    </Badge>
                  </div>

                  <div className="space-y-1.5">
                    {cat.items.map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => {
                          setSelectedCrdKey(item.key);
                          if (!expandedCategories.includes(cat.category)) {
                            setExpandedCategories((prev) => [...prev, cat.category]);
                          }
                        }}
                        className="w-full flex items-center justify-between p-2 rounded-md hover:bg-muted/60 text-left transition-colors cursor-pointer group"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="text-xs font-medium text-foreground group-hover:text-primary transition-colors truncate">
                            {item.label}
                          </div>
                          <div className="text-xs text-muted-foreground font-mono truncate">
                            {item.crdInfo.group}
                          </div>
                        </div>
                        <div className="flex items-center gap-1 shrink-0 ml-2">
                          <Badge variant="outline" className="text-xs font-mono py-0 px-1">
                            {item.crdInfo.version}
                          </Badge>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      );
    }

    if (resourcesLoading) {
      return (
        <div className="p-4">
          <SkeletonTable rows={5} columns={4} />
        </div>
      );
    }

    if (resourcesError) {
      return (
        <div className="p-4">
          <EmptyState
            icon={AlertTriangle}
            title="Failed to load resources"
            description={parseApiError(resourcesError).message}
          />
        </div>
      );
    }

    // 200 + info = CRD vanished / unavailable post-discovery
    if (resourcesData?.info && resources.length === 0) {
      return (
        <div className="p-4">
          <EmptyState
            icon={AlertTriangle}
            title="Resource type unavailable"
            description={resourcesData.info}
          />
        </div>
      );
    }

    if (resources.length === 0) {
      return (
        <div className="p-4">
          <EmptyState
            icon={Globe}
            title="No instances found"
            description={`No ${selectedCrdInfo?.display_name ?? selectedCrdInfo?.kind ?? selectedCrdKey} instances in ${selectedNamespace === 'all' ? 'all namespaces' : `namespace "${selectedNamespace}"`}.`}
          />
        </div>
      );
    }

    return (
      <div className="flex-1 overflow-auto">
        <CNFResourceTable
          resources={resources}
          selectedResource={selectedResource}
          onSelectResource={setSelectedResource}
          onDescribe={handleDescribe}
          borderDefault={borderDefault}
        />
      </div>
    );
  };

  const renderTopologyContent = () => {
    if (selectedNamespace === 'all') {
      return (
        <div className="flex flex-col items-center justify-center h-full p-6 text-center">
          <div className="max-w-md space-y-4">
            <div className="p-3 bg-primary/10 rounded-full w-12 h-12 flex items-center justify-center mx-auto text-primary">
              <Network className="h-6 w-6" />
            </div>
            <h3 className="text-lg font-semibold text-foreground">Select a Namespace for Topology</h3>
            <p className="text-xs text-muted-foreground">
              Telco and CNF topology graphs map intra-namespace and secondary interface connections. Choose a namespace below to visualize:
            </p>
            <div className="flex flex-wrap justify-center gap-1.5 pt-2 max-h-48 overflow-y-auto">
              {namespaces.map((ns) => {
                const nsName = typeof ns === 'string' ? ns : ns.name;
                return (
                  <button
                    key={nsName}
                    type="button"
                    onClick={() => setSelectedNamespace(nsName)}
                    className="px-2.5 py-1 text-xs rounded-md border border-border bg-card hover:bg-primary/10 hover:border-primary/50 text-foreground transition-all cursor-pointer font-mono"
                  >
                    {nsName}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      );
    }

    if (topologyLoading) {
      return (
        <div className="flex items-center justify-center h-full">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Network className="h-4 w-4 animate-pulse" />
            Building topology…
          </div>
        </div>
      );
    }

    if (topologyError) {
      return (
        <div className="flex items-center justify-center h-full">
          <EmptyState
            icon={AlertTriangle}
            title="Topology unavailable"
            description={parseApiError(topologyError).message}
            size="sm"
          />
        </div>
      );
    }

    if (!topologyData) return null;

    return (
      <div className="flex-1 h-full overflow-hidden">
        <ResourceTopologyGraph
          key={`${topologyNamespace}-${selectedCluster}`}
          nodes={topologyData.nodes}
          edges={topologyData.edges}
          info={topologyData.info}
          onDescribeResource={handleDescribe}
        />
      </div>
    );
  };

  const renderToolbar = () => (
    <div className={cn('flex items-center justify-between px-4 py-2 border-b', borderDefault)}>
      <div className="flex items-center gap-2">
        {view === 'browser' ? (
          <>
            {selectedCrdKey && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs gap-1 text-muted-foreground hover:text-foreground mr-1"
                onClick={() => setSelectedCrdKey(null)}
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                Catalog
              </Button>
            )}
            <h2 className="text-sm font-semibold">
              {selectedCrdInfo
                ? (selectedCrdInfo.display_name ?? selectedCrdInfo.kind)
                : 'CNF & Network Functions'}
              {selectedCrdKey && resources.length > 0 && (
                <span className="text-muted-foreground"> ({resources.length})</span>
              )}
            </h2>
            {selectedCrdInfo && (
              <Badge variant="outline" className="text-xs font-mono">
                {selectedCrdInfo.group}
              </Badge>
            )}
          </>
        ) : (
          <h2 className="text-sm font-semibold">
            CNF Topology
            {topologyNamespace && (
              <span className="text-muted-foreground"> — {topologyNamespace}</span>
            )}
          </h2>
        )}
      </div>

      <div className="flex items-center gap-2">
        {selectedCrdInfo?.source === 'registry-enriched' && view === 'browser' && (
          <Badge variant="muted" className="text-xs">
            curated
          </Badge>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs gap-1.5 text-muted-foreground hover:text-foreground"
          onClick={() => navigate('/kubernetes')}
        >
          <ExternalLink className="h-3.5 w-3.5" />
          Raw K8s CRD Explorer
        </Button>
      </div>
    </div>
  );

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <ResourceExplorerLayout>
      <ResourcePageHeader
        title="CNF Resources"
        subtitle="Cloud-Native Network Functions & Telco Infrastructure — Multus secondary interfaces, SR-IOV, and CNF workloads"
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
        isRefreshing={false}
      >
        {selectedCluster && crdsData && crdsData.crds.length > 0 && (
          <Badge variant="outline" className="text-xs h-7">
            <Activity className="h-3 w-3 mr-1" />
            {crdsData.count} CRD{crdsData.count !== 1 ? 's' : ''}
          </Badge>
        )}
      </ResourcePageHeader>

      {/* View-mode tab strip — Browse vs Topology. Shared ResourceViewTabs
          idiom, identical to the Kubernetes/F5 BNK strips (D-020). */}
      {selectedCluster && clusterReachable && (
        <ResourceViewTabs
          aria-label="CNF view mode"
          active={view}
          onChange={(key) => setView(key as 'browser' | 'topology')}
          tabs={[
            { key: 'browser', label: 'Browse', icon: LayoutGrid, title: 'Browse custom resources by CRD' },
            { key: 'topology', label: 'Topology', icon: Network, title: 'Resource topology graph' },
          ]}
        />
      )}

      <ResourceExplorerLayout.Body>
        {/* Sidebar — CRD category navigation; only needed in browser view */}
        {selectedCluster && clusterReachable && categories.length > 0 && view === 'browser' && (
          <CNFSidebar
            categories={categories}
            selectedCrdKey={selectedCrdKey}
            onSelectCrd={handleSelectCrd}
            expandedCategories={expandedCategories}
            onToggleCategory={toggleCategory}
          />
        )}

        {/* Center content */}
        {!selectedProject ? (
          <ResourceExplorerLayout.Content className="flex items-center justify-center">
            <EmptyState
              icon={Server}
              title="No project selected"
              description="Select a project to view its clusters and Custom Resources."
            />
          </ResourceExplorerLayout.Content>
        ) : !selectedCluster ? (
          <ResourceExplorerLayout.Content className="flex items-center justify-center">
            <EmptyState
              icon={Server}
              title="No cluster selected"
              description="Select a cluster to browse its Custom Resources."
            />
          </ResourceExplorerLayout.Content>
        ) : (
          <>
            <ResourceExplorerLayout.Content className="flex flex-col overflow-hidden">
              <ConnectivityGate
                target={{
                  type: 'cluster',
                  id: selectedCluster,
                  displayName: allClusters.find((c) => c.id === selectedCluster)?.name,
                }}
              >
                {renderToolbar()}
                {view === 'topology' ? renderTopologyContent() : renderCenterContent()}
              </ConnectivityGate>
            </ResourceExplorerLayout.Content>

            {/* Detail panel */}
            <ResourceExplorerLayout.DetailPanel open={!!selectedResource}>
              {selectedResource && (
                <CNFDetailPanel
                  resource={selectedResource}
                  onClose={() => setSelectedResource(null)}
                  onDescribe={handleDescribe}
                />
              )}
            </ResourceExplorerLayout.DetailPanel>
          </>
        )}
      </ResourceExplorerLayout.Body>

      {/* Describe dialog */}
      {selectedCluster && (
        <ResourceDescribeViewer
          open={describeOpen}
          onOpenChange={setDescribeOpen}
          resource={resourceToDescribe}
          clusterId={selectedCluster}
          namespace={resourceToDescribe?.metadata?.namespace}
        />
      )}
    </ResourceExplorerLayout>
  );
}
