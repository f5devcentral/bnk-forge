/**
 * Individual Helm release item in sidebar
 */

import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { getReleaseStatusColor, type HelmReleaseWithCluster } from '@/lib/helm-grouping';

interface ReleaseItemProps {
  release: HelmReleaseWithCluster;
  selected: boolean;
  onSelect: () => void;
}

export function ReleaseItem({ release, selected, onSelect }: ReleaseItemProps) {
  const statusColor = getReleaseStatusColor(release.status);

  return (
    <button
      onClick={onSelect}
      className={cn(
        'w-full flex items-center gap-2 px-4 py-2 transition-all',
        'text-sm text-left group',
        selected
          ? 'bg-accent border-l-2 border-primary'
          : 'hover:bg-accent/50 border-l-2 border-transparent'
      )}
      title={`${release.name}\n${release.chart}\n${release.status}`}
      aria-label={`Release ${release.name} in ${release.namespace} namespace, status: ${release.status}`}
      aria-current={selected ? 'true' : undefined}
    >
      {/* Status Dot with pulse for pending states */}
      <div className="relative flex-shrink-0">
        <div
          className={cn(
            'w-2 h-2 rounded-full transition-all',
            statusColor,
            release.status.includes('pending') && 'animate-pulse'
          )}
        />
      </div>

      {/* Release Name */}
      <div className="flex-1 min-w-0 flex flex-col">
        <span className="truncate font-medium">{release.name}</span>
        <span className="text-xs text-muted-foreground truncate opacity-0 group-hover:opacity-100 transition-opacity">
          {release.chart}
        </span>
      </div>

      {/* Revision Badge */}
      <Badge
        variant="outline"
        className={cn(
          "text-xs flex-shrink-0 transition-colors",
          selected && "bg-primary/10"
        )}
      >
        v{release.revision}
      </Badge>
    </button>
  );
}
