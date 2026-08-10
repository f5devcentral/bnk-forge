/**
 * SnapshotHistory — Timeline view for config snapshots (UX-011).
 *
 * Shows a chronological list of config snapshots for a project:
 * - Each entry: timestamp, trigger badge, label, created_by, resource/module counts
 * - Actions: Restore, View Config, Compare, Delete
 * - Manual snapshot creation with optional label
 * - Diff viewer using colored <pre> blocks (same approach as DriftDetailPanel)
 *
 * Design: Feels like a git log — scannable, each entry is a safe point to return to.
 *
 * D-020: token-pure surfaces (`bg-card`, `border-border`, `text-muted-foreground`)
 * and semantic status colors. No `useTheme`/`useThemeClasses` plumbing.
 */

import { useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { formatTimeAgo } from '@/lib/time-utils';
import {
  useSnapshots,
  useCreateSnapshot,
  useRestoreSnapshot,
  useDeleteSnapshot,
  useDiffSnapshots,
} from '@/hooks/useSnapshots';
import type {
  ConfigSnapshotSummary,
  SnapshotTrigger,
  SnapshotDiffResponse,
} from '@/types/snapshots';
import { useSnapshot } from '@/hooks/useSnapshots';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
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
import {
  Camera,
  RotateCcw,
  Eye,
  GitCompare,
  Trash2,
  Clock,
  User,
  Package,
  Layers,
  ChevronDown,
  ChevronUp,
  X,
  History,
  Plus,
  AlertCircle,
} from 'lucide-react';


// ============================================================================
// Props
// ============================================================================

interface SnapshotHistoryProps {
  projectId: number;
}


// ============================================================================
// Trigger Badge Config — token-pure variants
// ============================================================================

type TriggerVariant = 'default' | 'success' | 'warning' | 'info';

const TRIGGER_CONFIG: Record<SnapshotTrigger, { label: string; variant: TriggerVariant }> = {
  'manual': { label: 'Manual', variant: 'default' },
  'auto-before-apply': { label: 'Pre-Apply', variant: 'success' },
  'auto-before-destroy': { label: 'Pre-Destroy', variant: 'warning' },
  'auto-before-upgrade': { label: 'Pre-Upgrade', variant: 'info' },
};

function TriggerBadge({ trigger }: { trigger: SnapshotTrigger }) {
  const config = TRIGGER_CONFIG[trigger] || TRIGGER_CONFIG['manual'];
  return (
    <Badge variant={config.variant} className="text-xs font-medium">
      {config.label}
    </Badge>
  );
}


// ============================================================================
// Config Viewer Modal
// ============================================================================

function ConfigViewer({
  snapshotId,
  onClose,
}: {
  snapshotId: number;
  onClose: () => void;
}) {
  const { data: snapshot, isLoading } = useSnapshot(snapshotId);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="relative w-full max-w-4xl max-h-[85vh] rounded-lg border border-border bg-card shadow-xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <div className="flex items-center gap-3">
            <Eye className="h-5 w-5 text-primary" />
            <h3 className="text-lg font-semibold text-foreground">Snapshot Config</h3>
            {snapshot && (
              <TriggerBadge trigger={snapshot.trigger} />
            )}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto p-6">
          {isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-64 w-full" />
            </div>
          ) : snapshot ? (
            <pre className="text-xs font-mono whitespace-pre-wrap rounded-lg p-4 overflow-auto bg-muted/50 text-foreground">
              {JSON.stringify(snapshot.config_data, null, 2)}
            </pre>
          ) : (
            <p className="text-center text-muted-foreground">Snapshot not found</p>
          )}
        </div>
      </div>
    </div>
  );
}


// ============================================================================
// Diff Viewer
// ============================================================================

function DiffViewer({
  diff,
  onClose,
}: {
  diff: SnapshotDiffResponse;
  onClose: () => void;
}) {
  const renderDiffLine = (line: string, idx: number) => {
    let lineClass = '';
    if (line.startsWith('+')) lineClass = 'text-success bg-success/10';
    else if (line.startsWith('-')) lineClass = 'text-destructive bg-destructive/10';
    else if (line.startsWith('~')) lineClass = 'text-warning bg-warning/10';
    return <div key={idx} className={cn('px-2 py-0.5', lineClass)}>{line}</div>;
  };

  // Build diff summary text
  const diffLines: string[] = [];
  if (diff.resources.only_in_a.length > 0) {
    diffLines.push(`--- Only in Snapshot A (${diff.snapshot_a.id}):`);
    diff.resources.only_in_a.forEach(r => diffLines.push(`- ${r}`));
  }
  if (diff.resources.only_in_b.length > 0) {
    diffLines.push(`+++ Only in Snapshot B (${diff.snapshot_b.id}):`);
    diff.resources.only_in_b.forEach(r => diffLines.push(`+ ${r}`));
  }
  if (diff.resources.changed.length > 0) {
    diffLines.push('~~~ Changed Resources:');
    diff.resources.changed.forEach(c => diffLines.push(`~ ${c.resource} (${c.kind})`));
  }
  if (diff.modules.only_in_a.length > 0) {
    diffLines.push(`--- Modules only in A:`);
    diff.modules.only_in_a.forEach(m => diffLines.push(`- ${m}`));
  }
  if (diff.modules.only_in_b.length > 0) {
    diffLines.push(`+++ Modules only in B:`);
    diff.modules.only_in_b.forEach(m => diffLines.push(`+ ${m}`));
  }
  if (diff.modules.changed.length > 0) {
    diffLines.push('~~~ Changed Module Variables:');
    diff.modules.changed.forEach(m => diffLines.push(`~ ${m.module_path}`));
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="relative w-full max-w-4xl max-h-[85vh] rounded-lg border border-border bg-card shadow-xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <div className="flex items-center gap-3">
            <GitCompare className="h-5 w-5 text-primary" />
            <h3 className="text-lg font-semibold text-foreground">Snapshot Diff</h3>
            <span className="text-sm text-muted-foreground">{diff.summary}</span>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Snapshot labels */}
        <div className="flex items-center gap-4 px-6 py-3 border-b border-border text-sm text-muted-foreground">
          <span>
            <strong className="text-destructive">A:</strong>{' '}
            Snapshot #{diff.snapshot_a.id}
            {diff.snapshot_a.label && ` — "${diff.snapshot_a.label}"`}
            {diff.snapshot_a.created_at && ` (${new Date(diff.snapshot_a.created_at).toLocaleString()})`}
          </span>
          <span>vs</span>
          <span>
            <strong className="text-success">B:</strong>{' '}
            Snapshot #{diff.snapshot_b.id}
            {diff.snapshot_b.label && ` — "${diff.snapshot_b.label}"`}
            {diff.snapshot_b.created_at && ` (${new Date(diff.snapshot_b.created_at).toLocaleString()})`}
          </span>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto p-6">
          {diff.total_diffs === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 border-2 border-dashed border-border rounded-lg text-muted-foreground">
              <GitCompare className="h-10 w-10 mb-3 opacity-50" />
              <p className="text-lg font-medium">No differences</p>
              <p className="text-sm">These two snapshots are identical.</p>
            </div>
          ) : (
            <pre className="text-xs font-mono rounded-lg overflow-auto bg-muted/50">
              {diffLines.map((line, idx) => renderDiffLine(line, idx))}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}


// ============================================================================
// Snapshot Entry (Timeline Row)
// ============================================================================

// Token-pure timeline dot color per trigger
const TRIGGER_DOT_CLASS: Record<SnapshotTrigger, { ring: string; dot: string }> = {
  'manual': { ring: 'border-primary bg-primary/20', dot: 'bg-primary' },
  'auto-before-apply': { ring: 'border-success bg-success/20', dot: 'bg-success' },
  'auto-before-destroy': { ring: 'border-warning bg-warning/20', dot: 'bg-warning' },
  'auto-before-upgrade': { ring: 'border-info bg-info/20', dot: 'bg-info' },
};

function SnapshotEntry({
  snapshot,
  isExpanded,
  onToggle,
  onViewConfig,
  onRestore,
  onCompare,
  onDelete,
  compareMode,
  isSelectedForCompare,
  onSelectForCompare,
}: {
  snapshot: ConfigSnapshotSummary;
  isExpanded: boolean;
  onToggle: () => void;
  onViewConfig: () => void;
  onRestore: () => void;
  onCompare: () => void;
  onDelete: () => void;
  compareMode: boolean;
  isSelectedForCompare: boolean;
  onSelectForCompare: () => void;
}) {
  const timeAgo = formatTimeAgo(snapshot.created_at);
  const dateStr = new Date(snapshot.created_at).toLocaleString();
  const dotClasses = TRIGGER_DOT_CLASS[snapshot.trigger] ?? TRIGGER_DOT_CLASS['manual'];

  return (
    <div className="relative pl-8 pb-6 group before:absolute before:left-[11px] before:top-6 before:bottom-0 before:w-[2px] before:bg-border">
      {/* Timeline dot */}
      <div className={cn(
        'absolute left-0 top-1.5 w-[24px] h-[24px] rounded-full border-2 flex items-center justify-center',
        dotClasses.ring,
      )}>
        <div className={cn('w-2 h-2 rounded-full', dotClasses.dot)} />
      </div>

      {/* Card */}
      <Card className={cn(
        'border border-border bg-card transition-colors cursor-pointer hover:bg-muted/50',
        isSelectedForCompare && 'ring-2 ring-primary',
      )}>
        {/* Header */}
        <div
          className="flex items-center justify-between px-4 py-3"
          onClick={compareMode ? onSelectForCompare : onToggle}
        >
          <div className="flex items-center gap-3 min-w-0">
            <TriggerBadge trigger={snapshot.trigger} />

            {snapshot.label && (
              <span className="font-medium truncate text-foreground">
                {snapshot.label}
              </span>
            )}

            <span className="text-sm flex items-center gap-1 text-muted-foreground">
              <Clock className="h-3.5 w-3.5" />
              <span title={dateStr}>{timeAgo}</span>
            </span>

            {snapshot.created_by && (
              <span className="text-sm flex items-center gap-1 text-muted-foreground">
                <User className="h-3.5 w-3.5" />
                {snapshot.created_by}
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            {/* Stats pills */}
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              {snapshot.resource_count > 0 && (
                <span className="flex items-center gap-1">
                  <Layers className="h-3 w-3" /> {snapshot.resource_count} resources
                </span>
              )}
              {snapshot.module_count > 0 && (
                <span className="flex items-center gap-1">
                  <Package className="h-3 w-3" /> {snapshot.module_count} modules
                </span>
              )}
            </div>

            {!compareMode && (
              isExpanded
                ? <ChevronUp className="h-4 w-4 text-muted-foreground" />
                : <ChevronDown className="h-4 w-4 text-muted-foreground" />
            )}
          </div>
        </div>

        {/* Expanded actions */}
        {isExpanded && !compareMode && (
          <div className="px-4 py-3 border-t border-border flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={onRestore}>
              <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
              Restore
            </Button>
            <Button variant="outline" size="sm" onClick={onViewConfig}>
              <Eye className="h-3.5 w-3.5 mr-1.5" />
              View Config
            </Button>
            <Button variant="outline" size="sm" onClick={onCompare}>
              <GitCompare className="h-3.5 w-3.5 mr-1.5" />
              Compare
            </Button>
            <div className="flex-1" />
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive hover:bg-destructive/10"
              onClick={onDelete}
            >
              <Trash2 className="h-3.5 w-3.5 mr-1.5" />
              Delete
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}


// ============================================================================
// Main Component
// ============================================================================

export function SnapshotHistory({ projectId }: SnapshotHistoryProps) {
  // Data
  const { data, isLoading, isError, error, refetch } = useSnapshots(projectId);
  const createMutation = useCreateSnapshot();
  const restoreMutation = useRestoreSnapshot();
  const deleteMutation = useDeleteSnapshot();
  const diffMutation = useDiffSnapshots();

  // UI state
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [newLabel, setNewLabel] = useState('');
  const [showLabelInput, setShowLabelInput] = useState(false);
  const [viewingConfigId, setViewingConfigId] = useState<number | null>(null);
  const [diffResult, setDiffResult] = useState<SnapshotDiffResponse | null>(null);

  // Compare mode
  const [compareMode, setCompareMode] = useState(false);
  const [compareA, setCompareA] = useState<number | null>(null);
  const [compareB, setCompareB] = useState<number | null>(null);

  // Confirm dialogs
  const [restoreConfirmId, setRestoreConfirmId] = useState<number | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);

  const snapshots = data?.snapshots ?? [];

  // Handlers
  const handleCreateSnapshot = useCallback(() => {
    createMutation.mutate(
      { projectId, data: { label: newLabel || undefined } },
      {
        onSuccess: () => {
          setNewLabel('');
          setShowLabelInput(false);
          refetch();
        },
      },
    );
  }, [projectId, newLabel, createMutation, refetch]);

  const handleRestore = useCallback((snapshotId: number) => {
    restoreMutation.mutate(snapshotId, {
      onSuccess: () => {
        setRestoreConfirmId(null);
        refetch();
      },
    });
  }, [restoreMutation, refetch]);

  const handleDelete = useCallback((snapshotId: number) => {
    deleteMutation.mutate(snapshotId, {
      onSuccess: () => {
        setDeleteConfirmId(null);
        refetch();
      },
    });
  }, [deleteMutation, refetch]);

  const handleStartCompare = useCallback((snapshotId: number) => {
    setCompareMode(true);
    setCompareA(snapshotId);
    setCompareB(null);
    setExpandedId(null);
  }, []);

  const handleSelectForCompare = useCallback((snapshotId: number) => {
    if (!compareA) {
      setCompareA(snapshotId);
    } else if (compareA === snapshotId) {
      // Deselect
      setCompareA(null);
    } else {
      setCompareB(snapshotId);
      // Run diff
      diffMutation.mutate(
        { snapshot_a_id: compareA, snapshot_b_id: snapshotId },
        {
          onSuccess: (result) => {
            setDiffResult(result);
            setCompareMode(false);
            setCompareA(null);
            setCompareB(null);
          },
        },
      );
    }
  }, [compareA, diffMutation]);

  const exitCompareMode = useCallback(() => {
    setCompareMode(false);
    setCompareA(null);
    setCompareB(null);
  }, []);

  // ============================================================================
  // Render
  // ============================================================================

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <History className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-semibold text-foreground">Config Snapshots</h2>
          {data && (
            <span className="text-sm text-muted-foreground">
              {data.total} snapshot{data.total !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {compareMode && (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm bg-muted/50 text-muted-foreground">
              <GitCompare className="h-4 w-4 text-primary" />
              <span>Select two snapshots to compare</span>
              <Button variant="ghost" size="sm" className="h-6 px-2" onClick={exitCompareMode}>
                Cancel
              </Button>
            </div>
          )}

          {showLabelInput ? (
            <div className="flex items-center gap-2">
              <Input
                value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)}
                placeholder="Label (optional)"
                className="h-9 w-48"
                onKeyDown={(e) => e.key === 'Enter' && handleCreateSnapshot()}
                autoFocus
              />
              <Button
                size="sm"
                onClick={handleCreateSnapshot}
                disabled={createMutation.isPending}
              >
                <Camera className="h-3.5 w-3.5 mr-1.5" />
                {createMutation.isPending ? 'Saving...' : 'Save'}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => { setShowLabelInput(false); setNewLabel(''); }}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ) : (
            <Button
              size="sm"
              onClick={() => setShowLabelInput(true)}
              disabled={createMutation.isPending}
            >
              <Plus className="h-3.5 w-3.5 mr-1.5" />
              Create Snapshot
            </Button>
          )}
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="pl-8">
              <Skeleton className="h-16 w-full rounded-lg" />
            </div>
          ))}
        </div>
      )}

      {/* Error state */}
      {isError && (
        <div className="flex flex-col items-center justify-center py-12 border border-destructive/30 bg-destructive/10 rounded-lg">
          <AlertCircle className="h-10 w-10 text-destructive mb-3" />
          <p className="text-sm font-medium text-destructive mb-1">Failed to load snapshots</p>
          <p className="text-xs text-muted-foreground">
            {error instanceof Error ? error.message : 'An unexpected error occurred.'}
          </p>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !isError && snapshots.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 border-2 border-dashed border-border rounded-lg text-muted-foreground">
          <Camera className="h-12 w-12 mb-4 opacity-50" />
          <p className="text-lg font-medium mb-1">No snapshots yet</p>
          <p className="text-sm mb-4">
            Snapshots are created automatically before deployments and upgrades.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowLabelInput(true)}
          >
            <Plus className="h-3.5 w-3.5 mr-1.5" />
            Create First Snapshot
          </Button>
        </div>
      )}

      {/* Timeline */}
      {!isLoading && snapshots.length > 0 && (
        <div className="relative">
          {snapshots.map((snap) => (
            <SnapshotEntry
              key={snap.id}
              snapshot={snap}
              isExpanded={expandedId === snap.id}
              onToggle={() => setExpandedId(expandedId === snap.id ? null : snap.id)}
              onViewConfig={() => setViewingConfigId(snap.id)}
              onRestore={() => setRestoreConfirmId(snap.id)}
              onCompare={() => handleStartCompare(snap.id)}
              onDelete={() => setDeleteConfirmId(snap.id)}
              compareMode={compareMode}
              isSelectedForCompare={compareA === snap.id || compareB === snap.id}
              onSelectForCompare={() => handleSelectForCompare(snap.id)}
            />
          ))}
        </div>
      )}

      {/* Config Viewer Modal */}
      {viewingConfigId !== null && (
        <ConfigViewer
          snapshotId={viewingConfigId}
          onClose={() => setViewingConfigId(null)}
        />
      )}

      {/* Diff Viewer Modal */}
      {diffResult && (
        <DiffViewer
          diff={diffResult}
          onClose={() => setDiffResult(null)}
        />
      )}

      {/* Restore Confirmation Dialog */}
      <AlertDialog open={restoreConfirmId !== null} onOpenChange={() => setRestoreConfirmId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Restore Configuration?</AlertDialogTitle>
            <AlertDialogDescription>
              This will re-apply the cluster configuration from this snapshot using server-side apply.
              Existing resources matching the snapshot will be updated to match the saved state.
              {restoreConfirmId && (() => {
                const snap = snapshots.find(s => s.id === restoreConfirmId);
                if (!snap) return null;
                return (
                  <span className="block mt-2 font-medium">
                    Snapshot from {new Date(snap.created_at).toLocaleString()}
                    {snap.label && ` — "${snap.label}"`}
                  </span>
                );
              })()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => restoreConfirmId && handleRestore(restoreConfirmId)}
              disabled={restoreMutation.isPending}
            >
              {restoreMutation.isPending ? 'Restoring...' : 'Restore'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteConfirmId !== null} onOpenChange={() => setDeleteConfirmId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Snapshot?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete this config snapshot. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteConfirmId && handleDelete(deleteConfirmId)}
              className="bg-destructive hover:bg-destructive/90 text-destructive-foreground"
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}


// ============================================================================
// Helpers
// ============================================================================

// FE-005: getTimeAgo replaced with formatTimeAgo from lib/time-utils.ts
