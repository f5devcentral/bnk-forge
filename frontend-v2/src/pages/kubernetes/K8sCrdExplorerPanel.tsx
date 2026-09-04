import { useState, useMemo } from 'react';
import { useCrds, type CRDInfo } from '@/hooks/useCrds';
import { useClusterResources, useClusterNamespaces } from '@/hooks/useK8s';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { SkeletonTable } from '@/components/ui/skeleton-table';
import { Search, Database, Eye, RefreshCw, FileCode } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { K8sResource } from '@/types';

interface K8sCrdExplorerPanelProps {
  clusterId: number | null;
}

export function K8sCrdExplorerPanel({ clusterId }: K8sCrdExplorerPanelProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [selectedCrd, setSelectedCrd] = useState<CRDInfo | null>(null);
  const [selectedNamespace, setSelectedNamespace] = useState<string>('all');
  const [selectedInstance, setSelectedInstance] = useState<K8sResource | null>(null);

  const { data: crdsData, isLoading: crdsLoading, refetch: refetchCrds } = useCrds(clusterId ?? 0, {
    enabled: !!clusterId,
  });

  const { data: namespacesData } = useClusterNamespaces(clusterId ?? 0);
  const namespaces = useMemo(() => namespacesData?.namespaces ?? [], [namespacesData]);

  const {
    data: instancesData,
    isLoading: instancesLoading,
    refetch: refetchInstances,
  } = useClusterResources(
    clusterId ?? 0,
    selectedCrd ? selectedCrd.name : '',
    { namespace: selectedNamespace === 'all' ? undefined : selectedNamespace },
    { enabled: !!clusterId && !!selectedCrd }
  );

  const crds = useMemo(() => crdsData?.crds ?? [], [crdsData]);

  const groups = useMemo(() => {
    const map = new Map<string, number>();
    for (const crd of crds) {
      const g = crd.group || 'other';
      map.set(g, (map.get(g) || 0) + 1);
    }
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
  }, [crds]);

  const filteredCrds = useMemo(() => {
    return crds.filter((crd) => {
      if (selectedGroup && crd.group !== selectedGroup) return false;
      if (!searchTerm) return true;
      const term = searchTerm.toLowerCase();
      return (
        crd.name.toLowerCase().includes(term) ||
        crd.kind.toLowerCase().includes(term) ||
        crd.group.toLowerCase().includes(term) ||
        (crd.display_name && crd.display_name.toLowerCase().includes(term))
      );
    });
  }, [crds, selectedGroup, searchTerm]);

  if (!clusterId) {
    return (
      <EmptyState
        icon={Database}
        title="No Cluster Selected"
        description="Select a Kubernetes cluster above to browse its Custom Resource Definitions (CRDs)."
      />
    );
  }

  if (crdsLoading) {
    return <SkeletonTable rows={8} columns={4} />;
  }

  if (crds.length === 0) {
    return (
      <EmptyState
        icon={Database}
        title="No CRDs Found"
        description="No Custom Resource Definitions are currently installed or discovered on this cluster."
        action={{
          label: 'Refresh CRDs',
          onClick: () => void refetchCrds(),
          icon: <RefreshCw className="h-4 w-4 mr-1.5" />,
        }}
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Search & Groups Bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-72">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search CRDs by kind or group..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9 h-9"
          />
        </div>

        {groups.length > 0 && (
          <select
            value={selectedGroup || ''}
            onChange={(e) => setSelectedGroup(e.target.value || null)}
            className="h-9 rounded-md border border-border bg-card text-foreground px-3 text-sm"
            aria-label="Filter by API Group"
          >
            <option value="">All API Groups ({crds.length})</option>
            {groups.map(([group, count]) => (
              <option key={group} value={group}>
                {group} ({count})
              </option>
            ))}
          </select>
        )}

        <div className="ml-auto text-xs text-muted-foreground">
          Showing <span className="font-semibold text-foreground">{filteredCrds.length}</span> of{' '}
          <span className="font-semibold text-foreground">{crds.length}</span> CRDs
        </div>
      </div>

      {/* 2-Column Master-Detail Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Left Column: CRD Directory */}
        <div className="lg:col-span-5 rounded-lg border border-border bg-card overflow-hidden">
          <div className="p-3 border-b border-border bg-muted/30 font-medium text-xs text-muted-foreground flex items-center justify-between">
            <span>Custom Resource Definitions</span>
            <Badge variant="outline" className="text-xs">
              {filteredCrds.length}
            </Badge>
          </div>
          <div className="max-h-[600px] overflow-y-auto divide-y divide-border">
            {filteredCrds.map((crd) => {
              const isSelected = selectedCrd?.name === crd.name;
              return (
                <button
                  key={crd.name}
                  type="button"
                  onClick={() => {
                    setSelectedCrd(crd);
                    setSelectedInstance(null);
                  }}
                  className={cn(
                    'w-full text-left p-3 transition-colors flex items-start justify-between gap-2',
                    isSelected
                      ? 'bg-primary/10 border-l-4 border-l-primary'
                      : 'hover:bg-muted/40'
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-sm text-foreground truncate">
                        {crd.display_name || crd.kind}
                      </span>
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-mono">
                        {crd.namespaced ? 'Namespaced' : 'Cluster'}
                      </Badge>
                    </div>
                    <div className="text-xs font-mono text-muted-foreground truncate mt-0.5">
                      {crd.name}
                    </div>
                    <div className="text-[11px] text-muted-foreground truncate mt-1">
                      Group: <span className="text-foreground/80">{crd.group}</span>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right Column: Instances & Definition Inspector */}
        <div className="lg:col-span-7 space-y-4">
          {selectedCrd ? (
            <div className="rounded-lg border border-border bg-card p-4 space-y-4">
              <div className="flex items-start justify-between gap-3 border-b border-border pb-3">
                <div>
                  <h3 className="text-base font-semibold text-foreground flex items-center gap-2">
                    <Database className="h-4 w-4 text-primary" />
                    {selectedCrd.display_name || selectedCrd.kind}
                  </h3>
                  <div className="text-xs font-mono text-muted-foreground mt-0.5">
                    {selectedCrd.name} • {selectedCrd.group}
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  {selectedCrd.namespaced && namespaces.length > 0 && (
                    <select
                      value={selectedNamespace}
                      onChange={(e) => setSelectedNamespace(e.target.value)}
                      className="h-8 rounded-md border border-border bg-background text-foreground px-2 text-xs"
                      aria-label="Filter instances by namespace"
                    >
                      <option value="all">All Namespaces</option>
                      {namespaces.map((ns) => (
                        <option key={ns.name} value={ns.name}>
                          {ns.name}
                        </option>
                      ))}
                    </select>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8"
                    onClick={() => void refetchInstances()}
                  >
                    <RefreshCw className="h-3.5 w-3.5 mr-1" />
                    Refresh
                  </Button>
                </div>
              </div>

              {/* Instances list */}
              <div>
                {(() => {
                  const liveResources: K8sResource[] = instancesData?.resources ?? [];
                  return (
                    <>
                      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                        Live Resource Instances ({liveResources.length})
                      </div>

                      {instancesLoading ? (
                        <SkeletonTable rows={4} columns={3} />
                      ) : liveResources.length > 0 ? (
                        <div className="divide-y divide-border border border-border rounded-md overflow-hidden max-h-64 overflow-y-auto">
                          {liveResources.map((inst) => {
                            const isInstSelected = selectedInstance?.metadata?.name === inst.metadata?.name;
                            return (
                              <div
                                key={inst.metadata?.uid || inst.metadata?.name}
                                className={cn(
                                  'p-2.5 flex items-center justify-between gap-3 text-xs cursor-pointer transition-colors',
                                  isInstSelected ? 'bg-primary/10' : 'hover:bg-muted/40'
                                )}
                                onClick={() => setSelectedInstance(inst)}
                              >
                          <div>
                            <span className="font-semibold text-foreground">
                              {inst.metadata?.name}
                            </span>
                            {inst.metadata?.namespace && (
                              <span className="text-muted-foreground ml-2">
                                ns: {inst.metadata.namespace}
                              </span>
                            )}
                          </div>
                          <Button variant="ghost" size="sm" className="h-6 px-2 text-[11px]">
                            <Eye className="h-3 w-3 mr-1" />
                            Inspect
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="p-6 text-center border border-dashed border-border rounded-md text-xs text-muted-foreground">
                    No instances of <code className="text-foreground">{selectedCrd.kind}</code> found in this cluster / namespace scope.
                  </div>
                )}
                    </>
                  );
                })()}
              </div>

              {/* Instance Detail Viewer */}
              {selectedInstance && (
                <div className="border-t border-border pt-3 space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                    <FileCode className="h-3.5 w-3.5" />
                    Instance: {selectedInstance.metadata?.name}
                  </div>
                  <pre className="p-3 bg-muted/50 rounded-md text-[11px] font-mono overflow-x-auto max-h-72 border border-border text-foreground">
                    {JSON.stringify(selectedInstance, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          ) : (
            <div className="h-96 rounded-lg border border-dashed border-border flex flex-col items-center justify-center p-8 text-center">
              <Database className="h-10 w-10 text-muted-foreground mb-3" />
              <div className="text-sm font-semibold text-foreground">Select a Custom Resource Definition</div>
              <p className="text-xs text-muted-foreground mt-1 max-w-sm">
                Choose a CRD from the directory on the left to inspect its live instances, namespaces, and schema attributes.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default K8sCrdExplorerPanel;
