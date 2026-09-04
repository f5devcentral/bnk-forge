import { useState, useRef, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Search,
  X,
  Globe,
  Loader2,
  ExternalLink,
  Server,
  FolderGit2,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useGlobalSearch } from '@/hooks/useGlobalSearch';
import {
  getCloudProviderBadgeInfo,
  getClusterLocationInfo,
  getProjectLocationInfo,
} from '@/lib/aws-regions';
import type { Project, K8sCluster } from '@/types';

interface HeroOmniSearchProps {
  projects?: Project[];
  clusters?: K8sCluster[];
  className?: string;
  debounceMs?: number;
}

type SearchFilter = 'all' | 'fqdn' | 'aws' | 'azure' | 'gke' | 'metal';

export function HeroOmniSearch({
  projects = [],
  clusters = [],
  className,
  debounceMs = 250,
}: HeroOmniSearchProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<SearchFilter>('all');
  const [isOpen, setIsOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const { data: searchResults, isSearching } = useGlobalSearch(query, 25, debounceMs);

  // Global keyboard shortcut ('/' or 'Cmd+K' / 'Ctrl+K')
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't intercept if user is typing in another input or textarea
      const target = e.target as HTMLElement;
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable
      ) {
        if (e.key === 'Escape' && target === inputRef.current) {
          setIsOpen(false);
          inputRef.current?.blur();
        }
        return;
      }

      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
        setIsOpen(true);
      } else if (e.key === '/' && document.activeElement !== inputRef.current) {
        e.preventDefault();
        inputRef.current?.focus();
        setIsOpen(true);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Close dropdown on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Filter backend results according to selected chip
  const filteredIngresses = useMemo(() => {
    const list = searchResults?.ingresses || [];
    if (filter === 'all' || filter === 'fqdn') return list;
    if (filter === 'aws') return list.filter((i) => i.cloud_provider?.toLowerCase().includes('aws') || i.cloud_provider?.toLowerCase().includes('eks'));
    if (filter === 'azure') return list.filter((i) => i.cloud_provider?.toLowerCase().includes('azure') || i.cloud_provider?.toLowerCase().includes('aks'));
    if (filter === 'gke') return list.filter((i) => i.cloud_provider?.toLowerCase().includes('gcp') || i.cloud_provider?.toLowerCase().includes('gke'));
    if (filter === 'metal') return list.filter((i) => i.cloud_provider?.toLowerCase().includes('metal') || i.cloud_provider?.toLowerCase().includes('on-prem'));
    return list;
  }, [searchResults?.ingresses, filter]);

  const filteredClusters = useMemo(() => {
    if (filter === 'fqdn') return [];
    const qLower = query.trim().toLowerCase();
    const backendList = searchResults?.clusters || [];
    // Combine with local clusters when query matches
    const localMatches = qLower
      ? clusters
          .filter(
            (c) =>
              c.name.toLowerCase().includes(qLower) ||
              c.cloud_provider?.toLowerCase().includes(qLower) ||
              c.region?.toLowerCase().includes(qLower)
          )
          .map((c) => ({
            id: c.id,
            name: c.name,
            cloud_provider: c.cloud_provider,
            region: c.region,
            status: c.status,
            detected_platform_profile: c.detected_platform_profile,
          }))
      : [];

    const combinedMap = new Map<number, any>();
    backendList.forEach((c) => combinedMap.set(c.id, c));
    localMatches.forEach((c) => {
      if (!combinedMap.has(c.id)) combinedMap.set(c.id, c);
    });

    const list = Array.from(combinedMap.values());
    if (filter === 'all') return list;
    if (filter === 'aws') return list.filter((c) => c.cloud_provider?.toLowerCase().includes('aws') || c.cloud_provider?.toLowerCase().includes('eks'));
    if (filter === 'azure') return list.filter((c) => c.cloud_provider?.toLowerCase().includes('azure') || c.cloud_provider?.toLowerCase().includes('aks'));
    if (filter === 'gke') return list.filter((c) => c.cloud_provider?.toLowerCase().includes('gcp') || c.cloud_provider?.toLowerCase().includes('gke'));
    if (filter === 'metal') return list.filter((c) => c.cloud_provider?.toLowerCase().includes('metal') || c.cloud_provider?.toLowerCase().includes('on-prem'));
    return list;
  }, [searchResults?.clusters, clusters, query, filter]);

  const filteredProjects = useMemo(() => {
    if (filter === 'fqdn') return [];
    const qLower = query.trim().toLowerCase();
    const backendList = searchResults?.projects || [];
    const localMatches = qLower
      ? projects
          .filter(
            (p) =>
              p.name.toLowerCase().includes(qLower) ||
              p.cloud_provider?.toLowerCase().includes(qLower) ||
              p.region?.toLowerCase().includes(qLower)
          )
          .map((p) => ({
            id: p.id,
            name: p.name,
            description: p.description,
            cloud_provider: p.cloud_provider,
            region: p.region,
            module_count: p.module_count,
            deployed_count: p.deployed_count,
            failed_count: p.failed_count,
          }))
      : [];

    const combinedMap = new Map<number, any>();
    backendList.forEach((p) => combinedMap.set(p.id, p));
    localMatches.forEach((p) => {
      if (!combinedMap.has(p.id)) combinedMap.set(p.id, p);
    });

    const list = Array.from(combinedMap.values());
    if (filter === 'all') return list;
    if (filter === 'aws') return list.filter((p) => p.cloud_provider?.toLowerCase().includes('aws'));
    if (filter === 'azure') return list.filter((p) => p.cloud_provider?.toLowerCase().includes('azure'));
    if (filter === 'gke') return list.filter((p) => p.cloud_provider?.toLowerCase().includes('gcp') || p.cloud_provider?.toLowerCase().includes('gke'));
    if (filter === 'metal') return list.filter((p) => p.cloud_provider?.toLowerCase().includes('metal') || p.cloud_provider?.toLowerCase().includes('on-prem'));
    return list;
  }, [searchResults?.projects, projects, query, filter]);

  const totalResultsCount =
    filteredIngresses.length + filteredClusters.length + filteredProjects.length;

  const handleSelectIngress = (clusterId: number, namespace: string, name: string) => {
    setIsOpen(false);
    navigate(
      `/kubernetes?cluster=${clusterId}&namespace=${encodeURIComponent(
        namespace
      )}&resource=ingresses&name=${encodeURIComponent(name)}&view=advanced`
    );
  };

  const handleSelectCluster = (clusterId: number) => {
    setIsOpen(false);
    navigate(`/kubernetes?cluster=${clusterId}&view=advanced`);
  };

  const handleSelectProject = (projectId: number) => {
    setIsOpen(false);
    navigate(`/projects/${projectId}`);
  };

  const hasQuery = query.trim().length >= 2;

  return (
    <div ref={containerRef} className={cn('relative w-full', className)}>
      {/* Search Input Box */}
      <div className="relative flex items-center bg-card border border-border/70 rounded-xl shadow-sm focus-within:ring-2 focus-within:ring-primary/30 focus-within:border-primary transition-all">
        <div className="pl-4 text-muted-foreground flex items-center justify-center pointer-events-none">
          {isSearching ? (
            <Loader2 className="w-5 h-5 animate-spin text-primary" />
          ) : (
            <Search className="w-5 h-5 text-muted-foreground" />
          )}
        </div>

        <Input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!isOpen) setIsOpen(true);
          }}
          onFocus={() => setIsOpen(true)}
          placeholder="Search FQDN (e.g. api.example.com), Hostname, Cluster, or Project..."
          className="h-12 border-0 bg-transparent text-base focus-visible:ring-0 focus-visible:ring-offset-0 px-3 placeholder:text-muted-foreground/70"
        />

        {query ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setQuery('');
              inputRef.current?.focus();
            }}
            className="mr-2 h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
          >
            <X className="w-4 h-4" />
          </Button>
        ) : (
          <div className="hidden sm:flex items-center gap-1 mr-3 pointer-events-none">
            <kbd className="inline-flex items-center justify-center px-2 py-0.5 text-[11px] font-mono text-muted-foreground bg-muted rounded border border-border">
              ⌘K
            </kbd>
            <span className="text-[11px] text-muted-foreground/60">or</span>
            <kbd className="inline-flex items-center justify-center px-1.5 py-0.5 text-[11px] font-mono text-muted-foreground bg-muted rounded border border-border">
              /
            </kbd>
          </div>
        )}
      </div>

      {/* Quick Filter Chips */}
      <div className="flex items-center gap-1.5 mt-2.5 overflow-x-auto pb-1 text-xs">
        <span className="text-muted-foreground/80 font-medium mr-1 text-[11px]">Filter:</span>
        <button
          type="button"
          onClick={() => setFilter('all')}
          className={cn(
            'px-2.5 py-1 rounded-full font-medium transition-colors cursor-pointer text-xs',
            filter === 'all'
              ? 'bg-primary text-primary-foreground shadow-xs'
              : 'bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground'
          )}
        >
          All
        </button>
        <button
          type="button"
          onClick={() => setFilter('fqdn')}
          className={cn(
            'px-2.5 py-1 rounded-full font-medium transition-colors cursor-pointer text-xs flex items-center gap-1',
            filter === 'fqdn'
              ? 'bg-primary text-primary-foreground shadow-xs'
              : 'bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground'
          )}
        >
          <Globe className="w-3 h-3" /> Ingresses & VIPs
        </button>
        <button
          type="button"
          onClick={() => setFilter('aws')}
          className={cn(
            'px-2.5 py-1 rounded-full font-medium transition-colors cursor-pointer text-xs flex items-center gap-1',
            filter === 'aws'
              ? 'bg-amber-500 text-white shadow-xs'
              : 'bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground'
          )}
        >
          AWS
        </button>
        <button
          type="button"
          onClick={() => setFilter('azure')}
          className={cn(
            'px-2.5 py-1 rounded-full font-medium transition-colors cursor-pointer text-xs flex items-center gap-1',
            filter === 'azure'
              ? 'bg-sky-500 text-white shadow-xs'
              : 'bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground'
          )}
        >
          Azure
        </button>
        <button
          type="button"
          onClick={() => setFilter('gke')}
          className={cn(
            'px-2.5 py-1 rounded-full font-medium transition-colors cursor-pointer text-xs flex items-center gap-1',
            filter === 'gke'
              ? 'bg-blue-500 text-white shadow-xs'
              : 'bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground'
          )}
        >
          GKE
        </button>
        <button
          type="button"
          onClick={() => setFilter('metal')}
          className={cn(
            'px-2.5 py-1 rounded-full font-medium transition-colors cursor-pointer text-xs flex items-center gap-1',
            filter === 'metal'
              ? 'bg-emerald-600 text-white shadow-xs'
              : 'bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground'
          )}
        >
          Bare Metal
        </button>
      </div>

      {/* Dropdown Results Overlay */}
      {isOpen && hasQuery && (
        <div className="absolute left-0 right-0 top-[calc(100%+8px)] z-50 bg-popover/95 backdrop-blur-md border border-border rounded-xl shadow-xl max-h-[480px] overflow-y-auto p-2">
          {totalResultsCount === 0 ? (
            isSearching ? (
              <div className="flex items-center justify-center py-10 text-muted-foreground gap-2">
                <Loader2 className="w-5 h-5 animate-spin text-primary" />
                <span>Searching multi-cloud clusters and resources...</span>
              </div>
            ) : (
              <div className="text-center py-8 px-4 text-muted-foreground">
                <Globe className="w-8 h-8 mx-auto mb-2 opacity-30" />
                <p className="font-medium text-foreground">No matches found for &quot;{query}&quot;</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Try searching by FQDN (e.g. <code className="bg-muted px-1 py-0.5 rounded">api.company.com</code>), cluster name, or service name.
                </p>
              </div>
            )
          ) : (
            <div className="space-y-4">
              {isSearching && (
                <div className="flex items-center gap-2 px-2 py-1 text-xs text-muted-foreground border-b border-border/40">
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
                  <span>Updating multi-cloud results...</span>
                </div>
              )}
              {/* Category 1: Ingresses & Hostnames */}
              {filteredIngresses.length > 0 && (
                <div>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center justify-between">
                    <span className="flex items-center gap-1.5">
                      <Globe className="w-3.5 h-3.5 text-primary" /> Ingresses, VIPs & FQDNs ({filteredIngresses.length})
                    </span>
                  </div>
                  <div className="space-y-1">
                    {filteredIngresses.map((item, idx) => {
                      const providerBadge = getCloudProviderBadgeInfo(item.cloud_provider);
                      const location = getClusterLocationInfo(item.cloud_provider, item.region);

                      return (
                        <div
                          key={`ing-${item.cluster_id}-${item.namespace}-${item.name}-${idx}`}
                          onClick={() => handleSelectIngress(item.cluster_id, item.namespace, item.name)}
                          className="group flex items-center justify-between p-2.5 rounded-lg hover:bg-accent/80 cursor-pointer transition-colors"
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            <div className="w-8 h-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0">
                              <Globe className="w-4 h-4" />
                            </div>
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="font-semibold text-sm text-foreground truncate group-hover:text-primary transition-colors">
                                  {item.matched_host || item.name}
                                </span>
                                <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                                  {item.kind}
                                </Badge>
                              </div>
                              <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5 truncate">
                                <span>ns: {item.namespace}</span>
                                <span>•</span>
                                <span>target: {item.target_service || item.name}</span>
                              </div>
                            </div>
                          </div>

                          <div className="flex items-center gap-2 shrink-0">
                            <Badge className={providerBadge.badgeClass} variant={providerBadge.badgeVariant}>
                              {providerBadge.shortLabel}
                            </Badge>
                            {location?.flag && (
                              <span className="text-sm" title={location.label}>
                                {location.flag}
                              </span>
                            )}
                            <span className="text-xs text-muted-foreground font-mono hidden md:inline">
                              {item.cluster_name}
                            </span>
                            <ExternalLink className="w-4 h-4 text-muted-foreground group-hover:text-primary transition-colors ml-1" />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Category 2: Clusters */}
              {filteredClusters.length > 0 && (
                <div>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center justify-between">
                    <span className="flex items-center gap-1.5">
                      <Server className="w-3.5 h-3.5 text-primary" /> Kubernetes Clusters ({filteredClusters.length})
                    </span>
                  </div>
                  <div className="space-y-1">
                    {filteredClusters.map((cluster) => {
                      const providerBadge = getCloudProviderBadgeInfo(cluster.cloud_provider);
                      const location = getClusterLocationInfo(cluster.cloud_provider, cluster.region);

                      return (
                        <div
                          key={`cluster-${cluster.id}`}
                          onClick={() => handleSelectCluster(cluster.id)}
                          className="group flex items-center justify-between p-2.5 rounded-lg hover:bg-accent/80 cursor-pointer transition-colors"
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            <div className="w-8 h-8 rounded-lg bg-sky-500/10 text-sky-500 flex items-center justify-center shrink-0">
                              <Server className="w-4 h-4" />
                            </div>
                            <div className="min-w-0">
                              <span className="font-semibold text-sm text-foreground truncate group-hover:text-primary transition-colors">
                                {cluster.name}
                              </span>
                              <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
                                {cluster.node_count !== undefined && cluster.node_count !== null && (
                                  <span>{cluster.node_count} nodes</span>
                                )}
                                {cluster.detected_platform_profile && (
                                  <>
                                    <span>•</span>
                                    <span>{cluster.detected_platform_profile}</span>
                                  </>
                                )}
                              </div>
                            </div>
                          </div>

                          <div className="flex items-center gap-2 shrink-0">
                            <Badge className={providerBadge.badgeClass} variant={providerBadge.badgeVariant}>
                              {providerBadge.shortLabel}
                            </Badge>
                            {location?.flag && (
                              <span className="text-sm" title={location.label}>
                                {location.flag}
                              </span>
                            )}
                            <ExternalLink className="w-4 h-4 text-muted-foreground group-hover:text-primary transition-colors ml-1" />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Category 3: Projects */}
              {filteredProjects.length > 0 && (
                <div>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center justify-between">
                    <span className="flex items-center gap-1.5">
                      <FolderGit2 className="w-3.5 h-3.5 text-primary" /> Projects ({filteredProjects.length})
                    </span>
                  </div>
                  <div className="space-y-1">
                    {filteredProjects.map((project) => {
                      const providerBadge = getCloudProviderBadgeInfo(project.cloud_provider);
                      const location = getProjectLocationInfo(project.cloud_provider, project.region);

                      return (
                        <div
                          key={`proj-${project.id}`}
                          onClick={() => handleSelectProject(project.id)}
                          className="group flex items-center justify-between p-2.5 rounded-lg hover:bg-accent/80 cursor-pointer transition-colors"
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            <div className="w-8 h-8 rounded-lg bg-emerald-500/10 text-emerald-500 flex items-center justify-center shrink-0">
                              <FolderGit2 className="w-4 h-4" />
                            </div>
                            <div className="min-w-0">
                              <span className="font-semibold text-sm text-foreground truncate group-hover:text-primary transition-colors">
                                {project.name}
                              </span>
                              {project.description && (
                                <p className="text-xs text-muted-foreground truncate max-w-md">
                                  {project.description}
                                </p>
                              )}
                            </div>
                          </div>

                          <div className="flex items-center gap-2 shrink-0">
                            <span className="text-xs text-muted-foreground">
                              {project.deployed_count}/{project.module_count} modules
                            </span>
                            <Badge className={providerBadge.badgeClass} variant={providerBadge.badgeVariant}>
                              {providerBadge.shortLabel}
                            </Badge>
                            {location?.flag && (
                              <span className="text-sm" title={location.label}>
                                {location.flag}
                              </span>
                            )}
                            <ExternalLink className="w-4 h-4 text-muted-foreground group-hover:text-primary transition-colors ml-1" />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
