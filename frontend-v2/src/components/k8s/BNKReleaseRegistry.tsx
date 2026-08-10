/**
 * BNK Release Registry — read-only list of GA release rows with source citations
 * and a "Sync from OCI" action button (issue #217).
 */

import { ExternalLink, RefreshCw, Database, Loader2, AlertTriangle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useBnkReleases, useSyncBnkReleasesFromOci } from '@/hooks/useK8sBnk';
import type { BnkReleaseRegistryItem } from '@/types';

interface BNKReleaseRegistryProps {
  /** Cluster ID used for the OCI sync action (requires cne_pull_secret). */
  clusterId: number;
}

const SOURCE_BADGE: Record<string, string> = {
  clouddocs: 'bg-info/10 text-info border-info/20',
  oci: 'bg-primary/10 text-primary border-primary/20',
  observed: 'bg-warning/10 text-warning border-warning/20',
  manual: 'bg-muted text-muted-foreground',
};

export function BNKReleaseRegistry({ clusterId }: BNKReleaseRegistryProps) {
  const { data, isLoading, isError } = useBnkReleases(true);
  const syncMutation = useSyncBnkReleasesFromOci(clusterId);

  const textMuted = 'text-muted-foreground';
  const textDefault = 'text-foreground';

  if (isLoading) {
    return (
      <div className="rounded-lg border p-4 bg-card">
        <div className="flex items-center gap-2 py-3">
          <Loader2 className="h-4 w-4 animate-spin text-primary" />
          <span className={cn('text-sm', textMuted)}>Loading release registry...</span>
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-4">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-destructive" />
          <span className="text-sm text-destructive">Failed to load release registry.</span>
        </div>
      </div>
    );
  }

  const releases = data?.releases ?? [];

  return (
    <div className="rounded-lg border p-4 bg-card space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Database className="h-5 w-5 text-primary" />
          <h3 className={cn('font-semibold', textDefault)}>
            BNK Release Registry
            <span className={cn('ml-2 text-sm font-normal', textMuted)}>
              ({releases.length} active rows)
            </span>
          </h3>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="gap-2"
          onClick={() => syncMutation.mutate()}
          disabled={syncMutation.isPending}
        >
          {syncMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Sync from OCI
        </Button>
      </div>

      {releases.length === 0 ? (
        <p className={cn('text-sm', textMuted)}>No active release rows. Run migration v2_132.</p>
      ) : (
        <div className="divide-y divide-border">
          {releases.map((rel: BnkReleaseRegistryItem) => (
            <div key={rel.id} className="py-3 first:pt-0 last:pb-0">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={cn('font-semibold text-sm', textDefault)}>{rel.ga_label}</span>
                    <Badge
                      variant="outline"
                      className={cn('text-xs', SOURCE_BADGE[rel.source_type] || SOURCE_BADGE.manual)}
                    >
                      {rel.source_type}
                    </Badge>
                    {rel.flo_version_prefix && (
                      <span className={cn('text-xs font-mono', textMuted)}>
                        FLO {rel.flo_version_prefix}.x
                      </span>
                    )}
                    {rel.manifest_version && (
                      <span className={cn('text-xs font-mono', textMuted)}>
                        manifest {rel.manifest_version}
                      </span>
                    )}
                  </div>
                  {(rel.min_k8s || rel.max_k8s) && (
                    <p className={cn('text-xs mt-0.5', textMuted)}>
                      K8s {rel.min_k8s}–{rel.max_k8s}
                    </p>
                  )}
                </div>
                {rel.source_url && (
                  <a
                    href={rel.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline flex-shrink-0"
                    title="Source reference"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
