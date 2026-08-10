/**
 * Namespace group with collapsible releases
 */

import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { ChevronRight, Folder } from 'lucide-react';
import { ReleaseItem } from './ReleaseItem';
import type { NamespaceWithReleases, HelmReleaseWithCluster } from '@/lib/helm-grouping';

interface NamespaceGroupProps {
  namespace: NamespaceWithReleases;
  clusterId: number;
  expanded: boolean;
  onToggle: () => void;
  selectedRelease: HelmReleaseWithCluster | null;
  onSelectRelease: (release: HelmReleaseWithCluster) => void;
}

export function NamespaceGroup({
  namespace,
  expanded,
  onToggle,
  selectedRelease,
  onSelectRelease,
}: NamespaceGroupProps) {
  const releaseCount = namespace.releases.length;

  return (
    <div>
      {/* Namespace Header */}
      <button
        onClick={onToggle}
        className={cn(
          'w-full flex items-center gap-2 px-4 py-2 hover:bg-accent transition-colors',
          'text-sm group'
        )}
        title={`${namespace.name} (${releaseCount} release${releaseCount !== 1 ? 's' : ''})`}
        aria-expanded={expanded}
        aria-label={`${expanded ? 'Collapse' : 'Expand'} namespace ${namespace.name}`}
      >
        <ChevronRight
          className={cn(
            'w-3 h-3 flex-shrink-0 transition-transform duration-200',
            expanded && 'rotate-90'
          )}
        />
        <Folder className="w-3 h-3 flex-shrink-0 opacity-70 group-hover:opacity-100 transition-opacity" />
        <span className="flex-1 text-left truncate">{namespace.name}</span>
        <Badge variant="outline" className="text-xs flex-shrink-0">
          {releaseCount}
        </Badge>
      </button>

      {/* Releases (when expanded) - Animated */}
      <div
        className={cn(
          'ml-8 overflow-hidden transition-all duration-200',
          expanded ? 'max-h-[1000px] opacity-100' : 'max-h-0 opacity-0'
        )}
      >
        {namespace.releases.length === 0 ? (
          <div className="px-4 py-2 text-xs text-muted-foreground italic">
            No releases
          </div>
        ) : (
          namespace.releases.map((release) => (
            <ReleaseItem
              key={`${release.cluster_id}-${release.namespace}-${release.name}`}
              release={release}
              selected={
                selectedRelease?.name === release.name &&
                selectedRelease?.namespace === release.namespace &&
                selectedRelease?.cluster_id === release.cluster_id
              }
              onSelect={() => onSelectRelease(release)}
            />
          ))
        )}
      </div>
    </div>
  );
}
