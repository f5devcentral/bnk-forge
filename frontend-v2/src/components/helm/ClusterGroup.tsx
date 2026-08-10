/**
 * Cluster group with collapsible namespaces
 */

import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { ChevronRight, Server } from 'lucide-react';
import { NamespaceGroup } from './NamespaceGroup';
import { getTotalReleaseCount, type ClusterWithReleases, type HelmReleaseWithCluster } from '@/lib/helm-grouping';

interface ClusterGroupProps {
  cluster: ClusterWithReleases;
  expanded: boolean;
  onToggle: () => void;
  selectedRelease: HelmReleaseWithCluster | null;
  onSelectRelease: (release: HelmReleaseWithCluster) => void;
  expandedNamespaces: Set<string>;
  onToggleNamespace: (key: string) => void;
}

export function ClusterGroup({
  cluster,
  expanded,
  onToggle,
  selectedRelease,
  onSelectRelease,
  expandedNamespaces,
  onToggleNamespace,
}: ClusterGroupProps) {
  const releaseCount = getTotalReleaseCount(cluster);

  return (
    <div>
      {/* Cluster Header */}
      <button
        onClick={onToggle}
        className={cn(
          'w-full flex items-center gap-2 px-4 py-2 hover:bg-accent transition-colors',
          'text-sm font-medium group'
        )}
        title={`${cluster.name} (${releaseCount} release${releaseCount !== 1 ? 's' : ''})`}
        aria-expanded={expanded}
        aria-label={`${expanded ? 'Collapse' : 'Expand'} cluster ${cluster.name}`}
      >
        <ChevronRight
          className={cn(
            'w-4 h-4 flex-shrink-0 transition-transform duration-200',
            expanded && 'rotate-90'
          )}
        />
        <Server className="w-4 h-4 flex-shrink-0 opacity-70 group-hover:opacity-100 transition-opacity" />
        <span className="flex-1 text-left truncate">{cluster.name}</span>
        <Badge variant="secondary" className="flex-shrink-0">
          {releaseCount}
        </Badge>
      </button>

      {/* Namespaces (when expanded) - Animated */}
      <div
        className={cn(
          'ml-6 overflow-hidden transition-all duration-200',
          expanded ? 'max-h-[2000px] opacity-100' : 'max-h-0 opacity-0'
        )}
      >
        {cluster.namespaces.length === 0 ? (
          <div className="px-4 py-2 text-xs text-muted-foreground italic">
            No releases deployed
          </div>
        ) : (
          cluster.namespaces.map((ns) => (
            <NamespaceGroup
              key={`${cluster.id}-${ns.name}`}
              namespace={ns}
              clusterId={cluster.id}
              expanded={expandedNamespaces.has(`${cluster.id}:${ns.name}`)}
              onToggle={() => onToggleNamespace(`${cluster.id}:${ns.name}`)}
              selectedRelease={selectedRelease}
              onSelectRelease={onSelectRelease}
            />
          ))
        )}
      </div>
    </div>
  );
}
