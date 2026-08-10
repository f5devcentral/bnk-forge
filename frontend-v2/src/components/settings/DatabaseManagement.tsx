import { useState } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Loader2, RefreshCw, Database, Trash2, Sparkles, HardDrive, Table, ChevronDown } from 'lucide-react';
import { useDatabaseStats, useCleanupDatabase, useVacuumDatabase } from '@/hooks/useSystem';
import { notify, notifyError } from '@/lib/notify';
import { cn } from '@/lib/utils';
import type { CleanupType } from '@/types/system';

// D-020: token-pure — no isDark, no raw palette classes.

export default function DatabaseManagement() {
  const { data: dbStats, isLoading, refetch } = useDatabaseStats();
  const cleanupMutation = useCleanupDatabase();
  const vacuumMutation = useVacuumDatabase();

  const [cleanupType, setCleanupType] = useState<CleanupType>('deployment_logs');
  const [olderThanDays, setOlderThanDays] = useState<number>(30);
  const [isOpen, setIsOpen] = useState(false);

  const handleCleanup = () => {
    if (olderThanDays < 7) {
      notify.error('Cannot delete records newer than 7 days');
      return;
    }

    const typeLabels: Record<CleanupType, string> = {
      deployment_logs: 'deployment logs',
      audit_logs: 'audit logs',
      completed_tasks: 'completed tasks',
    };

    const label = typeLabels[cleanupType as CleanupType];

    if (
      confirm(
        `Delete ${label} older than ${olderThanDays} days? This action cannot be undone.`
      )
    ) {
      cleanupMutation.mutate(
        { type: cleanupType, olderThanDays },
        {
          onSuccess: (data: unknown) => {
            const d = data as { deleted: number; freed_mb: number };
            notify.success(
              `Deleted ${d.deleted} record(s), freed ${d.freed_mb}MB`,
              undefined,
              { category: 'system' },
            );
            refetch();
          },
          onError: (error: unknown) => {
            notifyError(error);
          },
        }
      );
    }
  };

  const handleVacuum = () => {
    if (
      confirm(
        'Run VACUUM ANALYZE on the database? This will reclaim space and update statistics. May take time on large databases.'
      )
    ) {
      vacuumMutation.mutate(undefined, {
        onSuccess: (data: unknown) => {
          const d = data as { status: string; duration_seconds?: number; message?: string };
          if (d.status === 'success') {
            notify.success(
              `Database vacuum completed in ${d.duration_seconds}s`,
              undefined,
              { category: 'system' },
            );
          } else {
            notify.info(d.message || 'Vacuum skipped', undefined, { category: 'system' });
          }
          refetch();
        },
        onError: (error: unknown) => {
          notifyError(error);
        },
      });
    }
  };

  const formatSize = (mb: number) => {
    if (mb < 1) return `${Math.round(mb * 1024)}KB`;
    if (mb < 1024) return `${mb.toFixed(2)}MB`;
    return `${(mb / 1024).toFixed(2)}GB`;
  };

  const formatNumber = (num: number) => {
    return num.toLocaleString();
  };

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <SectionCard>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-start justify-between text-left -m-2 p-2 rounded-md hover:bg-accent/50 transition-colors"
          >
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Database management
              </p>
              <p className="text-sm text-muted-foreground mt-1">
                Monitor database size, cleanup old records, and optimize performance
              </p>
            </div>
            <ChevronDown className={cn(
              'h-5 w-5 transition-transform duration-200 text-muted-foreground shrink-0 mt-1',
              isOpen && 'rotate-180',
            )} />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="space-y-6 pt-4">
            {isLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : dbStats ? (
              <>
                {/* Database Overview */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-foreground">
                      Database Overview
                    </h3>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => refetch()}
                      disabled={isLoading}
                    >
                      <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
                      Refresh
                    </Button>
                  </div>

                  <div className="p-4 rounded-lg border border-border bg-muted/50">
                    <div className="flex items-center gap-3">
                      <HardDrive className="h-8 w-8 text-muted-foreground" />
                      <div>
                        <div className="text-sm text-muted-foreground">
                          Total Database Size
                        </div>
                        <div className="text-2xl font-bold text-foreground">
                          {formatSize(dbStats.size_mb)}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Table Statistics */}
                <div>
                  <h3 className="text-sm font-semibold mb-3 text-foreground">
                    Table Statistics
                  </h3>
                  <div className="space-y-2">
                    {Object.entries(dbStats.tables).map(([tableName, stats]) => (
                      <div
                        key={tableName}
                        className="flex items-center justify-between p-3 rounded-lg border border-border bg-card"
                      >
                        <div className="flex items-center gap-3">
                          <Table className="h-5 w-5 text-muted-foreground" />
                          <div>
                            <div className="font-medium text-foreground">
                              {tableName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              {formatNumber(stats.rows)} rows
                            </div>
                          </div>
                        </div>
                        <Badge variant="outline">
                          {formatSize(stats.size_mb)}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Cleanup Tools */}
                <div className="pt-4 border-t border-border">
                  <h3 className="text-sm font-semibold mb-3 text-foreground">
                    Cleanup Tools
                  </h3>

                  <div className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div>
                        <Label htmlFor="cleanup-type">
                          Record Type
                        </Label>
                        <Select
                          value={cleanupType}
                          onValueChange={(value) => setCleanupType(value as CleanupType)}
                        >
                          <SelectTrigger id="cleanup-type">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="deployment_logs">Deployment Logs</SelectItem>
                            <SelectItem value="audit_logs">Audit Logs</SelectItem>
                            <SelectItem value="completed_tasks">Completed Tasks</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>

                      <div>
                        <Label htmlFor="older-than">
                          Older Than (days)
                        </Label>
                        <Input
                          id="older-than"
                          type="number"
                          min={7}
                          value={olderThanDays}
                          onChange={(e) => setOlderThanDays(parseInt(e.target.value) || 7)}
                        />
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={handleCleanup}
                        disabled={cleanupMutation.isPending || olderThanDays < 7}
                      >
                        {cleanupMutation.isPending ? (
                          <>
                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                            Cleaning...
                          </>
                        ) : (
                          <>
                            <Trash2 className="h-4 w-4 mr-2" />
                            Delete Old Records
                          </>
                        )}
                      </Button>

                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleVacuum}
                        disabled={vacuumMutation.isPending}
                      >
                        {vacuumMutation.isPending ? (
                          <>
                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                            Optimizing...
                          </>
                        ) : (
                          <>
                            <Sparkles className="h-4 w-4 mr-2" />
                            Vacuum & Optimize
                          </>
                        )}
                      </Button>
                    </div>
                  </div>

                  <div className="rounded-lg border border-warning/20 bg-warning/10 p-4 mt-4">
                    <p className="text-sm text-warning">
                      <strong>Note:</strong> Cleanup operations cannot be undone. Records older than 7 days can be deleted.
                      VACUUM may take time on large databases.
                    </p>
                  </div>
                </div>
              </>
            ) : (
              <div className="text-center py-8 text-muted-foreground">
                <Database className="h-12 w-12 mx-auto mb-4 opacity-50" />
                <p>Database statistics unavailable</p>
                <p className="text-xs mt-2">Check database connection</p>
              </div>
            )}
          </div>
        </CollapsibleContent>
      </SectionCard>
    </Collapsible>
  );
}
