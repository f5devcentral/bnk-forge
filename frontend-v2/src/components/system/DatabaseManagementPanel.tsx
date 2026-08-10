import { useState } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { notify } from '@/lib/notify';
import { useDatabaseStats, useCleanupDatabase, useVacuumDatabase } from '@/hooks/useSystem';
import { Database, Trash2, Sparkles, RefreshCw } from 'lucide-react';
import type { CleanupType } from '@/types/system';

export function DatabaseManagementPanel() {
  const { data, isLoading, error, refetch } = useDatabaseStats();
  const cleanupMutation = useCleanupDatabase();
  const vacuumMutation = useVacuumDatabase();

  const [cleanupDialogOpen, setCleanupDialogOpen] = useState(false);
  const [cleanupType, setCleanupType] = useState<CleanupType>('deployment_logs');
  const [cleanupDays, setCleanupDays] = useState(30);

  const handleCleanup = async () => {
    try {
      const result = await cleanupMutation.mutateAsync({
        type: cleanupType,
        olderThanDays: cleanupDays,
      });

      notify.success('Cleanup Successful', `Deleted ${result.deleted} records, freed ${result.freed_mb.toFixed(2)} MB`, { category: 'system' });

      setCleanupDialogOpen(false);
      refetch();
    } catch (err) {
      notify.error('Cleanup Failed', err instanceof Error ? err.message : 'Unknown error');
    }
  };

  const handleVacuum = async () => {
    try {
      const result = await vacuumMutation.mutateAsync();

      if (result.status === 'success') {
        notify.success('Vacuum Completed', `Database optimized in ${result.duration_seconds.toFixed(1)}s`, { category: 'system' });
      } else {
        notify.info('Vacuum Skipped', result.message || 'Operation not needed', { category: 'system' });
      }

      refetch();
    } catch (err) {
      notify.error('Vacuum Failed', err instanceof Error ? err.message : 'Unknown error');
    }
  };

  if (isLoading) {
    return (
      <SectionCard title="Database Management">
        <p className="text-sm text-muted-foreground">Loading database statistics...</p>
      </SectionCard>
    );
  }

  if (error) {
    return (
      <SectionCard title="Database Management">
        <p className="text-sm text-destructive">Failed to load database statistics</p>
      </SectionCard>
    );
  }

  if (!data) return null;

  return (
    <>
      <SectionCard title="Database Management">
        <div className="flex items-center justify-between mb-6">
          <p className="text-sm text-muted-foreground">
            Cleanup old records and optimize database performance
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isLoading}
          >
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
        <div className="space-y-6">
          {/* Database Size */}
          <div className="flex items-center justify-between p-4 bg-muted/50 rounded-lg">
            <div className="flex items-center gap-3">
              <Database className="h-5 w-5 text-muted-foreground" />
              <div>
                <p className="font-semibold">Total Database Size</p>
                <p className="text-sm text-muted-foreground">All tables and indexes</p>
              </div>
            </div>
            <Badge variant="secondary" className="text-lg px-3 py-1">
              {data.size_mb.toFixed(1)} MB
            </Badge>
          </div>

          {/* Table Statistics */}
          <div className="space-y-3">
            <h3 className="font-semibold">Table Statistics</h3>
            <div className="space-y-2">
              {Object.entries(data.tables).map(([tableName, stats]) => (
                <div
                  key={tableName}
                  className="flex items-center justify-between p-3 border border-border rounded-lg"
                >
                  <div>
                    <p className="font-medium capitalize">
                      {tableName.replace(/_/g, ' ')}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      {stats.rows.toLocaleString()} rows
                    </p>
                  </div>
                  <Badge variant="outline">
                    {stats.size_mb.toFixed(2)} MB
                  </Badge>
                </div>
              ))}
            </div>
          </div>

          {/* Cleanup Actions */}
          <div className="space-y-3 pt-4 border-t border-border">
            <h3 className="font-semibold">Cleanup Operations</h3>
            <div className="grid grid-cols-2 gap-3">
              <Button
                variant="outline"
                onClick={() => {
                  setCleanupType('deployment_logs');
                  setCleanupDialogOpen(true);
                }}
                disabled={cleanupMutation.isPending}
                className="h-auto py-4 flex flex-col items-start gap-1"
              >
                <div className="flex items-center gap-2">
                  <Trash2 className="h-4 w-4" />
                  <span>Cleanup Deployment Logs</span>
                </div>
                <span className="text-xs text-muted-foreground">
                  Remove old execution logs
                </span>
              </Button>

              <Button
                variant="outline"
                onClick={() => {
                  setCleanupType('audit_logs');
                  setCleanupDialogOpen(true);
                }}
                disabled={cleanupMutation.isPending}
                className="h-auto py-4 flex flex-col items-start gap-1"
              >
                <div className="flex items-center gap-2">
                  <Trash2 className="h-4 w-4" />
                  <span>Cleanup Audit Logs</span>
                </div>
                <span className="text-xs text-muted-foreground">
                  Remove old audit entries
                </span>
              </Button>

              <Button
                variant="outline"
                onClick={() => {
                  setCleanupType('completed_tasks');
                  setCleanupDialogOpen(true);
                }}
                disabled={cleanupMutation.isPending}
                className="h-auto py-4 flex flex-col items-start gap-1"
              >
                <div className="flex items-center gap-2">
                  <Trash2 className="h-4 w-4" />
                  <span>Cleanup Completed Tasks</span>
                </div>
                <span className="text-xs text-muted-foreground">
                  Remove old task records
                </span>
              </Button>

              <Button
                variant="outline"
                onClick={handleVacuum}
                disabled={vacuumMutation.isPending}
                className="h-auto py-4 flex flex-col items-start gap-1"
              >
                <div className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4" />
                  <span>Optimize Database</span>
                </div>
                <span className="text-xs text-muted-foreground">
                  Run VACUUM ANALYZE
                </span>
              </Button>
            </div>
          </div>
        </div>
      </SectionCard>

      {/* Cleanup Confirmation Dialog */}
      <AlertDialog open={cleanupDialogOpen} onOpenChange={setCleanupDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm Database Cleanup</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete records from{' '}
              <strong>{cleanupType.replace(/_/g, ' ')}</strong>.
            </AlertDialogDescription>
          </AlertDialogHeader>

          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="cleanup-days">Delete records older than (days)</Label>
              <Input
                id="cleanup-days"
                type="number"
                min={7}
                value={cleanupDays}
                onChange={(e) => setCleanupDays(parseInt(e.target.value) || 30)}
              />
              <p className="text-sm text-muted-foreground">
                Minimum: 7 days. Records newer than this will be preserved.
              </p>
            </div>
          </div>

          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleCleanup}
              disabled={cleanupMutation.isPending || cleanupDays < 7}
              className="bg-destructive hover:bg-destructive/90"
            >
              {cleanupMutation.isPending ? 'Cleaning...' : 'Delete Records'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
