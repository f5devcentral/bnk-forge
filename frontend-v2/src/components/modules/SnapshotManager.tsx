import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Camera,
  RefreshCw,
  RotateCcw,
  AlertTriangle,
  GitCommit,
  User,
  Database,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { ProjectModule, JsonValue } from '@/types';
import { notify, notifyError } from '@/lib/notify';

interface Snapshot {
  id: number;
  snapshot_type: string;
  description?: string;
  created_by: string;
  terraform_state_serial?: number;
  git_commit?: string;
  module_path: string;
  created_at: string;
  status?: string;
  variable_overrides?: Record<string, JsonValue>;
}

interface SnapshotManagerProps {
  module: ProjectModule;
}

export function SnapshotManager({ module }: SnapshotManagerProps) {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedSnapshot, setSelectedSnapshot] = useState<Snapshot | null>(null);
  const [rollbackDialogOpen, setRollbackDialogOpen] = useState(false);
  const [isRollingBack, setIsRollingBack] = useState(false);

  const loadSnapshots = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await api.getModuleSnapshots(module.id);
      setSnapshots(response.snapshots || []);
    } catch (err: unknown) {
      notifyError(err);
    } finally {
      setIsLoading(false);
    }
  }, [module.id]);

  useEffect(() => {
    loadSnapshots();
  }, [loadSnapshots]);

  const handleCreateSnapshot = async () => {
    try {
      await api.createModuleSnapshot(module.id, {
        description: 'Manual snapshot',
        created_by: 'user',
      });
      notify.success('Snapshot created successfully', undefined, { category: 'deployment' });
      loadSnapshots();
    } catch (err: unknown) {
      notifyError(err);
    }
  };

  const handleRollback = async () => {
    if (!selectedSnapshot) return;

    setIsRollingBack(true);
    try {
      await api.rollbackToSnapshot(module.id, selectedSnapshot.id, {
        auto_approve: true,
      });
      notify.success('Rollback initiated successfully', undefined, { category: 'deployment' });
      setRollbackDialogOpen(false);
      setSelectedSnapshot(null);
    } catch (err: unknown) {
      notifyError(err);
    } finally {
      setIsRollingBack(false);
    }
  };

  const getSnapshotTypeBadge = (type: string): 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' => {
    const variants: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
      pre_apply: 'default',
      pre_destroy: 'destructive',
      manual: 'secondary',
    };
    return variants[type] || 'outline';
  };

  const getSnapshotTypeLabel = (type: string) => {
    const labels: Record<string, string> = {
      pre_apply: 'Pre-Apply',
      pre_destroy: 'Pre-Destroy',
      manual: 'Manual',
    };
    return labels[type] || type;
  };

  const formatTimestamp = (timestamp: string) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;

    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Camera className="h-5 w-5" />
                Snapshots & Rollback
              </CardTitle>
              <CardDescription>
                Create snapshots and rollback to previous states
              </CardDescription>
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={loadSnapshots} disabled={isLoading}>
                <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
                Refresh
              </Button>
              <Button size="sm" onClick={handleCreateSnapshot}>
                <Camera className="h-4 w-4 mr-2" />
                Create Snapshot
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading && snapshots.length === 0 ? (
            <div className="text-center py-12">
              <RefreshCw className="h-12 w-12 animate-spin mx-auto mb-4 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">Loading snapshots...</p>
            </div>
          ) : snapshots.length === 0 ? (
            <Alert>
              <Camera className="h-4 w-4" />
              <AlertDescription>
                No snapshots found. Snapshots are automatically created before apply/destroy operations.
                You can also create manual snapshots anytime.
              </AlertDescription>
            </Alert>
          ) : (
            <ScrollArea className="h-[500px]">
              <div className="space-y-3">
                {snapshots.map((snapshot) => (
                  <Card key={snapshot.id} className="border-2 hover:border-primary/50 transition-colors">
                    <CardContent className="pt-4">
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex items-center gap-2">
                          <Badge variant={getSnapshotTypeBadge(snapshot.snapshot_type)}>
                            {getSnapshotTypeLabel(snapshot.snapshot_type)}
                          </Badge>
                          {snapshot.status && (
                            <Badge variant="outline" className="text-xs">
                              {snapshot.status}
                            </Badge>
                          )}
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {formatTimestamp(snapshot.created_at)}
                        </span>
                      </div>

                      {snapshot.description && (
                        <p className="text-sm mb-3">{snapshot.description}</p>
                      )}

                      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground mb-3">
                        {snapshot.created_by && (
                          <div className="flex items-center gap-1">
                            <User className="h-3 w-3" />
                            {snapshot.created_by}
                          </div>
                        )}
                        {snapshot.terraform_state_serial !== undefined && (
                          <div className="flex items-center gap-1">
                            <Database className="h-3 w-3" />
                            Serial {snapshot.terraform_state_serial}
                          </div>
                        )}
                        {snapshot.git_commit && (
                          <div className="flex items-center gap-1">
                            <GitCommit className="h-3 w-3" />
                            {snapshot.git_commit.substring(0, 7)}
                          </div>
                        )}
                      </div>

                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setSelectedSnapshot(snapshot);
                          setRollbackDialogOpen(true);
                        }}
                        className="w-full"
                      >
                        <RotateCcw className="h-4 w-4 mr-2" />
                        Rollback to This Snapshot
                      </Button>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </ScrollArea>
          )}
        </CardContent>
      </Card>

      {/* Rollback Confirmation Dialog */}
      <Dialog open={rollbackDialogOpen} onOpenChange={setRollbackDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-warning" />
              Confirm Rollback
            </DialogTitle>
            <DialogDescription>
              Are you sure you want to rollback to this snapshot?
            </DialogDescription>
          </DialogHeader>

          {selectedSnapshot && (
            <div className="space-y-3">
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  This will restore the module configuration and state from the snapshot.
                  Current changes will be lost unless you create a snapshot first.
                </AlertDescription>
              </Alert>

              <div className="border rounded-lg p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">Snapshot Type:</span>
                  <Badge variant={getSnapshotTypeBadge(selectedSnapshot.snapshot_type)}>
                    {getSnapshotTypeLabel(selectedSnapshot.snapshot_type)}
                  </Badge>
                </div>
                {selectedSnapshot.description && (
                  <div>
                    <span className="text-sm font-medium">Description:</span>
                    <p className="text-sm text-muted-foreground">{selectedSnapshot.description}</p>
                  </div>
                )}
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">Created:</span>
                  <span className="text-sm text-muted-foreground">
                    {formatTimestamp(selectedSnapshot.created_at)}
                  </span>
                </div>
                {selectedSnapshot.git_commit && (
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">Git Commit:</span>
                    <code className="text-xs bg-muted px-2 py-1 rounded">
                      {selectedSnapshot.git_commit.substring(0, 7)}
                    </code>
                  </div>
                )}
              </div>
            </div>
          )}

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setRollbackDialogOpen(false);
                setSelectedSnapshot(null);
              }}
              disabled={isRollingBack}
            >
              Cancel
            </Button>
            <Button
              onClick={handleRollback}
              disabled={isRollingBack}
            >
              {isRollingBack ? (
                <>
                  <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
                  Rolling Back...
                </>
              ) : (
                <>
                  <RotateCcw className="h-4 w-4 mr-2" />
                  Confirm Rollback
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
