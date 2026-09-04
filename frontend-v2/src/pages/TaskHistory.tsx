/**
 * Operations Log (Task History) page — D-020 redesign.
 *
 * Single-surface bnkhealth shape: bold heading, 7-day KPI strip, chip-filter row
 * (status), type + project selectors, single sortable table in a SectionCard.
 * Status conveyed by Badge variants only; row tints replaced with a 2px left
 * accent border for failed / in-progress rows.
 */

import { useState, useMemo, useEffect } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { SectionCard } from '@/components/ui/section-card';
import { Checkbox } from '@/components/ui/checkbox';
import { DestructiveConfirmDialog } from '@/components/ui/destructive-confirm-dialog';
import {
  useTasks,
  useTask,
  useCancelTask,
  useTaskStats,
  useDeleteTask,
  useArchiveTask,
  useBulkDeleteTasks,
  useBulkArchiveTasks,
  useCleanupOldTasks,
} from '@/hooks/useTasks';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import { useProjects } from '@/hooks/useProjects';
import { cn } from '@/lib/utils';
import { formatTimeAgo } from '@/lib/time-utils';
import type { Task } from '@/types';
import {
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  Search,
  AlertCircle,
  Terminal,
  Ban,
  Timer,
  MoreVertical,
  Eye,
  Archive,
  ArchiveRestore,
  Trash2,
  Eraser,
} from 'lucide-react';

const INITIAL_TASKS_LIMIT = 25;

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);

// Drives the shared type-to-confirm dialog for the destructive ops-log actions.
type ConfirmState =
  | { kind: 'delete-one'; taskId: number }
  | { kind: 'delete-bulk'; taskIds: number[] }
  | { kind: 'cleanup' }
  | null;

type StatusKey = 'completed' | 'in_progress' | 'failed' | 'cancelled' | 'queued';
type StatusVariant = 'success' | 'info' | 'destructive' | 'muted' | 'warning';

const STATUS_CONFIG: Record<
  StatusKey,
  { label: string; variant: StatusVariant; icon: typeof CheckCircle2; animate?: boolean }
> = {
  completed: { label: 'Completed', variant: 'success', icon: CheckCircle2 },
  in_progress: { label: 'Running', variant: 'info', icon: Loader2, animate: true },
  failed: { label: 'Failed', variant: 'destructive', icon: XCircle },
  cancelled: { label: 'Cancelled', variant: 'muted', icon: XCircle },
  queued: { label: 'Queued', variant: 'warning', icon: Clock },
};

// Task type → human label. Color of the badge no longer carries per-type tint;
// all type badges render as muted-outline to keep chrome calm.
const TYPE_LABELS: Record<string, string> = {
  init: 'Initialize',
  plan: 'Plan',
  apply: 'Apply',
  destroy: 'Destroy',
  clone: 'Clone',
  sync: 'Sync',
  refresh: 'Refresh',
  drift_check: 'Drift check',
  helm_install: 'Helm install',
  helm_upgrade: 'Helm upgrade',
  helm_rollback: 'Helm rollback',
  helm_uninstall: 'Helm uninstall',
  k8s_apply: 'K8s apply',
  operator_command: 'Operator',
  bnk_upgrade: 'BNK upgrade',
  qkview: 'QKView',
};

function typeLabel(type: string): string {
  return TYPE_LABELS[type] || type.replace(/_/g, ' ');
}

function StatusPill({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status as StatusKey] ?? STATUS_CONFIG.queued;
  const Icon = cfg.icon;
  return (
    <Badge variant={cfg.variant} className="gap-1">
      <Icon className={cn('h-3 w-3', cfg.animate && status === 'in_progress' && 'animate-spin')} />
      {cfg.label}
    </Badge>
  );
}

// Order shown in the chip row.
const STATUS_ORDER: StatusKey[] = ['completed', 'in_progress', 'failed', 'queued', 'cancelled'];

export interface TaskHistoryProps {
  projectId?: number;
  embedded?: boolean;
}

export default function TaskHistory({ projectId, embedded = false }: TaskHistoryProps = {}) {
  const { data: projects } = useProjects();

  const [selectedStatus, setSelectedStatus] = useState<string | null>(null);
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(projectId || null);
  const [searchTerm, setSearchTerm] = useState('');
  const [showAllTasks, setShowAllTasks] = useState(false);

  useEffect(() => {
    if (projectId !== undefined) {
      setSelectedProjectId(projectId);
    }
  }, [projectId]);

  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);
  const [showFullLogs, setShowFullLogs] = useState(false);

  const [showArchived, setShowArchived] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [confirmState, setConfirmState] = useState<ConfirmState>(null);

  const { refresh, isRefreshing } = usePageRefresh();
  const cancelTask = useCancelTask();
  const deleteTask = useDeleteTask();
  const archiveTask = useArchiveTask();
  const bulkDeleteTasks = useBulkDeleteTasks();
  const bulkArchiveTasks = useBulkArchiveTasks();
  const cleanupTasks = useCleanupOldTasks();

  const taskParams = {
    project_id: selectedProjectId || undefined,
    task_type: selectedType || undefined,
    status: selectedStatus || undefined,
    archived: showArchived ? true : undefined,
    limit: 100,
  };

  const { data: tasksData, isLoading, isError, error, refetch } = useTasks(taskParams);
  const { data: taskStats } = useTaskStats({ days: 7 });
  const { data: taskDetails } = useTask(
    selectedTaskId || 0,
    showFullLogs ? { log_tail: undefined } : undefined,
  );

  // Live wall-clock tick to compute the running duration for in-flight tasks
  // while the detail dialog is open.
  const [, setNowTick] = useState(0);
  const taskIsOngoing = !!taskDetails && !taskDetails.completed_at;
  useEffect(() => {
    if (!taskDetails || !taskIsOngoing) return;
    const id = setInterval(() => setNowTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [taskDetails, taskIsOngoing]);

  const filteredTasks = useMemo(() => {
    return (tasksData?.tasks || []).filter((task) => {
      if (!searchTerm) return true;
      const q = searchTerm.toLowerCase();
      return (
        task.task_type.toLowerCase().includes(q) ||
        task.project_name?.toLowerCase().includes(q) ||
        task.module_name?.toLowerCase().includes(q)
      );
    });
  }, [tasksData?.tasks, searchTerm]);

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = { all: tasksData?.tasks?.length || 0 };
    tasksData?.tasks?.forEach((t) => {
      counts[t.status] = (counts[t.status] || 0) + 1;
    });
    return counts;
  }, [tasksData?.tasks]);

  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    tasksData?.tasks?.forEach((t) => {
      counts[t.task_type] = (counts[t.task_type] || 0) + 1;
    });
    return counts;
  }, [tasksData?.tasks]);

  const limitedTasks = showAllTasks ? filteredTasks : filteredTasks.slice(0, INITIAL_TASKS_LIMIT);
  const hasMoreTasks = filteredTasks.length > INITIAL_TASKS_LIMIT;

  const allVisibleSelected =
    limitedTasks.length > 0 && limitedTasks.every((t) => selectedIds.has(t.id));

  const toggleSelectAll = () => {
    if (allVisibleSelected) {
      clearSelection();
    } else {
      setSelectedIds(new Set(limitedTasks.map((t) => t.id)));
    }
  };

  const handleCancelTask = (taskId: number) => {
    cancelTask.mutate(taskId);
  };

  const handleViewDetails = (task: Task) => {
    setSelectedTaskId(task.id);
    setSelectedTask(task);
    setShowFullLogs(false);
  };

  const toggleSelect = (taskId: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  };

  const clearSelection = () => setSelectedIds(new Set());

  const handleArchive = (taskId: number, archived: boolean) => {
    archiveTask.mutate({ taskId, archived });
  };

  // Execute whichever destructive action the confirm dialog was opened for.
  const runConfirmedAction = () => {
    if (!confirmState) return;
    if (confirmState.kind === 'delete-one') {
      deleteTask.mutate(confirmState.taskId);
    } else if (confirmState.kind === 'delete-bulk') {
      bulkDeleteTasks.mutate(confirmState.taskIds);
      clearSelection();
    } else if (confirmState.kind === 'cleanup') {
      cleanupTasks.mutate(undefined);
    }
    setConfirmState(null);
  };

  const confirmPending =
    deleteTask.isPending || bulkDeleteTasks.isPending || cleanupTasks.isPending;

  const formatDuration = (seconds?: number) => {
    if (seconds === undefined || seconds === null) return '—';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = Math.round(seconds % 60);
    return `${minutes}m ${remainingSeconds}s`;
  };

  const formatTimestamp = (timestamp?: string) => {
    if (!timestamp) return 'N/A';
    return new Date(timestamp.replace(' ', 'T')).toLocaleString();
  };

  const stats7d = {
    total: taskStats?.total_tasks || 0,
    completed: taskStats?.by_status?.completed || 0,
    failed: taskStats?.by_status?.failed || 0,
    inProgress:
      (taskStats?.by_status?.in_progress || 0) + (taskStats?.by_status?.queued || 0),
  };

  const chipKeys: string[] = ['all', ...STATUS_ORDER.filter((s) => (statusCounts[s] || 0) > 0)];
  const visibleTypes = Object.entries(typeCounts).sort((a, b) => b[1] - a[1]);

  return (
    <div className={cn(embedded ? 'space-y-6' : 'p-6 space-y-6 max-w-7xl mx-auto')}>
      {/* Header */}
      {!embedded && (
        <PageHeader
          title="Operations Log"
          subtitle={`Deployment, helm, and operator operations executed across all projects.${stats7d.total > 0 ? ` ${stats7d.total} in the last 7 days.` : ''}`}
          onRefresh={refresh}
          isRefreshing={isRefreshing}
        />
      )}

      {/* 7-day KPI strip */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Total (7d)', value: stats7d.total, tone: 'foreground' as const },
          { label: 'Completed', value: stats7d.completed, tone: 'success' as const },
          { label: 'Failed', value: stats7d.failed, tone: 'destructive' as const },
          { label: 'Active / queued', value: stats7d.inProgress, tone: 'info' as const },
        ].map((tile) => {
          const toneText =
            tile.tone === 'success'
              ? 'text-success'
              : tile.tone === 'destructive'
              ? 'text-destructive'
              : tile.tone === 'info'
              ? 'text-info'
              : 'text-foreground';
          return (
            <div key={tile.label} className="rounded-lg border border-border bg-card px-4 py-3">
              <div className="text-xs text-muted-foreground">{tile.label}</div>
              <div className={cn('text-2xl font-bold mt-1 tabular-nums', toneText)}>
                {tile.value}
              </div>
            </div>
          );
        })}
      </div>

      {/* Filters: chip row + search + type + project */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {chipKeys.map((key) => {
            const isAll = key === 'all';
            const selected = isAll ? selectedStatus === null : selectedStatus === key;
            const label = isAll ? 'All' : STATUS_CONFIG[key as StatusKey]?.label || key;
            const count = statusCounts[key] || 0;
            return (
              <button
                key={key}
                type="button"
                onClick={() => {
                  setSelectedStatus(isAll ? null : key);
                  setShowAllTasks(false);
                }}
                className={cn(
                  'inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm transition-colors',
                  selected
                    ? 'bg-foreground text-background border-foreground'
                    : 'bg-card text-muted-foreground border-border hover:text-foreground',
                )}
                aria-pressed={selected}
              >
                <span>{label}</span>
                <span
                  className={cn(
                    'text-xs tabular-nums',
                    selected ? 'text-background/70' : 'text-muted-foreground/70',
                  )}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="relative w-64">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search operations…"
              aria-label="Search operations"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-9 h-9"
            />
          </div>

          {visibleTypes.length > 0 && (
            <select
              value={selectedType || ''}
              onChange={(e) => {
                setSelectedType(e.target.value || null);
                setShowAllTasks(false);
              }}
              className="h-9 rounded-md border border-border bg-card text-foreground px-3 text-sm"
              aria-label="Filter by operation type"
            >
              <option value="">All types</option>
              {visibleTypes.map(([key, count]) => (
                <option key={key} value={key}>
                  {typeLabel(key)} ({count})
                </option>
              ))}
            </select>
          )}

          {!projectId && projects && projects.length > 0 && (
            <select
              value={selectedProjectId || ''}
              onChange={(e) =>
                setSelectedProjectId(e.target.value ? parseInt(e.target.value) : null)
              }
              className="h-9 rounded-md border border-border bg-card text-foreground px-3 text-sm"
              aria-label="Filter by project"
            >
              <option value="">All projects</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          )}

          <div className="ml-auto flex items-center gap-2">
            <Button
              variant={showArchived ? 'default' : 'outline'}
              size="sm"
              className="h-9"
              onClick={() => {
                setShowArchived((v) => !v);
                setShowAllTasks(false);
                clearSelection();
              }}
              aria-pressed={showArchived}
            >
              <Archive className="h-4 w-4 mr-1.5" />
              {showArchived ? 'Viewing archived' : 'Show archived'}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-9"
              onClick={() => setConfirmState({ kind: 'cleanup' })}
            >
              <Eraser className="h-4 w-4 mr-1.5" />
              Clean up old
            </Button>
          </div>
        </div>
      </div>

      {/* Bulk action bar */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 rounded-lg border border-border bg-muted/40 px-4 py-2">
          <span className="text-sm font-medium text-foreground">
            {selectedIds.size} selected
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                bulkArchiveTasks.mutate({ taskIds: [...selectedIds], archived: !showArchived });
                clearSelection();
              }}
            >
              {showArchived ? (
                <>
                  <ArchiveRestore className="h-4 w-4 mr-1.5" />
                  Unarchive
                </>
              ) : (
                <>
                  <Archive className="h-4 w-4 mr-1.5" />
                  Archive
                </>
              )}
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setConfirmState({ kind: 'delete-bulk', taskIds: [...selectedIds] })}
            >
              <Trash2 className="h-4 w-4 mr-1.5" />
              Delete
            </Button>
            <Button variant="ghost" size="sm" onClick={clearSelection}>
              Clear
            </Button>
          </div>
        </div>
      )}

      {/* Table */}
      {isError ? (
        <ErrorState error={error} onRetry={refetch} />
      ) : isLoading ? (
        <SectionCard compact>
          <div className="space-y-2">
            {[...Array(8)].map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-md" />
            ))}
          </div>
        </SectionCard>
      ) : filteredTasks.length === 0 ? (
        <EmptyState
          icon={Terminal}
          title="No operations found"
          description={
            searchTerm
              ? `No operations match "${searchTerm}"`
              : 'Operations will appear here as you deploy infrastructure, manage Helm releases, and run K8s commands.'
          }
        />
      ) : (
        <SectionCard
          title={`${filteredTasks.length} ${filteredTasks.length === 1 ? 'operation' : 'operations'}`}
          compact
        >
          <div className="overflow-x-auto -mx-4">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wider text-muted-foreground">
                  <th scope="col" className="px-4 py-2 w-10">
                    <Checkbox
                      checked={allVisibleSelected}
                      onCheckedChange={toggleSelectAll}
                      aria-label="Select all operations"
                    />
                  </th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Task</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Status</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Project / Module</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Duration</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Started</th>
                  <th scope="col" className="text-right font-medium px-4 py-2 w-28">{/* actions */}</th>
                </tr>
              </thead>
              <tbody>
                {limitedTasks.map((task) => (
                  <tr
                    key={task.id}
                    data-testid="task-row"
                    data-task-id={task.id}
                    className={cn(
                      'border-t border-border cursor-pointer hover:bg-muted/40 transition-colors',
                      task.status === 'failed' && 'border-l-2 border-l-destructive',
                      task.status === 'in_progress' && 'border-l-2 border-l-info',
                    )}
                    onClick={() => handleViewDetails(task)}
                  >
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        checked={selectedIds.has(task.id)}
                        onCheckedChange={() => toggleSelect(task.id)}
                        aria-label={`Select operation #${task.id}`}
                      />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-xs">
                          {typeLabel(task.task_type)}
                        </Badge>
                        <span className="text-sm font-mono text-muted-foreground tabular-nums">
                          #{task.id}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3" data-testid="task-status">
                      <StatusPill status={task.status} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col">
                        <span className="text-foreground font-medium">{task.project_name}</span>
                        {task.module_name && (
                          <span className="text-xs text-muted-foreground">
                            {task.module_name}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5 text-muted-foreground">
                        <Timer className="h-3.5 w-3.5" />
                        <span className="text-sm tabular-nums">
                          {formatDuration(task.duration_seconds)}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-muted-foreground">
                      {formatTimeAgo(task.created_at)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {(task.status === 'queued' || task.status === 'in_progress') && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleCancelTask(task.id);
                            }}
                          >
                            <Ban className="h-4 w-4 mr-1" />
                            Cancel
                          </Button>
                        )}
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-8 w-8 p-0"
                              aria-label="Task actions"
                            >
                              <MoreVertical className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              onClick={(e) => {
                                e.stopPropagation();
                                handleViewDetails(task);
                              }}
                            >
                              <Eye className="h-4 w-4 mr-2" />
                              View details
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={(e) => {
                                e.stopPropagation();
                                handleArchive(task.id, !task.archived);
                              }}
                            >
                              {task.archived ? (
                                <>
                                  <ArchiveRestore className="h-4 w-4 mr-2" />
                                  Unarchive
                                </>
                              ) : (
                                <>
                                  <Archive className="h-4 w-4 mr-2" />
                                  Archive
                                </>
                              )}
                            </DropdownMenuItem>
                            {TERMINAL_STATUSES.has(task.status) && (
                              <DropdownMenuItem
                                className="text-destructive focus:text-destructive"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setConfirmState({ kind: 'delete-one', taskId: task.id });
                                }}
                              >
                                <Trash2 className="h-4 w-4 mr-2" />
                                Delete
                              </DropdownMenuItem>
                            )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!showAllTasks && hasMoreTasks && (
            <div className="mt-4 flex justify-center">
              <Button variant="outline" size="sm" onClick={() => setShowAllTasks(true)}>
                Show {filteredTasks.length - INITIAL_TASKS_LIMIT} more operations
              </Button>
            </div>
          )}
          {showAllTasks && hasMoreTasks && (
            <div className="mt-4 flex justify-center">
              <Button variant="ghost" size="sm" onClick={() => setShowAllTasks(false)}>
                Show less
              </Button>
            </div>
          )}
        </SectionCard>
      )}

      {/* Task Details Dialog */}
      <Dialog
        open={!!selectedTask}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedTask(null);
            setSelectedTaskId(null);
            setShowFullLogs(false);
          }
        }}
      >
        <DialogContent className="max-w-4xl max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-foreground">
              <Terminal className="h-5 w-5 text-muted-foreground" />
              Task #{selectedTask?.id} — {typeLabel(selectedTask?.task_type ?? '')}
            </DialogTitle>
            <DialogDescription>View detailed logs and metadata for this task.</DialogDescription>
          </DialogHeader>
          <div className="flex-1 overflow-auto">
            {taskDetails && (
              <div className="space-y-4 pr-1">
                {/* Task Info */}
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="text-xs uppercase tracking-wider text-muted-foreground w-20">
                      Status
                    </span>
                    <StatusPill status={taskDetails.status} />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs uppercase tracking-wider text-muted-foreground w-20">
                      Project
                    </span>
                    <span className="text-foreground">{taskDetails.project_name}</span>
                  </div>
                  {taskDetails.module_name && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs uppercase tracking-wider text-muted-foreground w-20">
                        Module
                      </span>
                      <span className="text-foreground">{taskDetails.module_name}</span>
                    </div>
                  )}
                  <div className="flex items-center gap-2">
                    <span className="text-xs uppercase tracking-wider text-muted-foreground w-20">
                      Created
                    </span>
                    <span className="text-foreground/80">
                      {formatTimestamp(taskDetails.created_at)}
                    </span>
                  </div>
                  {taskDetails.started_at && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs uppercase tracking-wider text-muted-foreground w-20">
                        Started
                      </span>
                      <span className="text-foreground/80">
                        {formatTimestamp(taskDetails.started_at)}
                      </span>
                    </div>
                  )}
                  {taskDetails.completed_at && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs uppercase tracking-wider text-muted-foreground w-20">
                        Completed
                      </span>
                      <span className="text-foreground/80">
                        {formatTimestamp(taskDetails.completed_at)}
                      </span>
                    </div>
                  )}
                  {(taskDetails.started_at || taskDetails.duration_seconds !== undefined) && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs uppercase tracking-wider text-muted-foreground w-20">
                        Duration
                      </span>
                      <span className="text-foreground/80 tabular-nums">
                        {taskDetails.completed_at
                          ? formatDuration(taskDetails.duration_seconds)
                          : taskDetails.started_at
                          ? formatDuration(
                              Math.max(
                                0,
                                (Date.now() - new Date(taskDetails.started_at).getTime()) / 1000,
                              ),
                            )
                          : '—'}
                      </span>
                    </div>
                  )}
                </div>

                {/* Command */}
                {taskDetails.command && (
                  <div>
                    <h4 className="text-xs uppercase tracking-wider text-muted-foreground mb-2">
                      Command
                    </h4>
                    <pre className="p-3 rounded-md text-xs overflow-x-auto bg-muted/60 border border-border text-foreground/90">
                      {taskDetails.command}
                    </pre>
                  </div>
                )}

                {/* Error */}
                {taskDetails.error && (
                  <div>
                    <h4 className="flex items-center gap-2 text-xs uppercase tracking-wider text-destructive mb-2">
                      <AlertCircle className="h-3.5 w-3.5" />
                      Error
                    </h4>
                    <pre className="p-3 rounded-md text-xs overflow-x-auto bg-destructive/10 border border-destructive/20 text-destructive">
                      {taskDetails.error}
                    </pre>
                  </div>
                )}

                {/* Logs */}
                {taskDetails.logs && (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="text-xs uppercase tracking-wider text-muted-foreground">
                        Output logs
                      </h4>
                      {taskDetails.logs_truncated && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setShowFullLogs(!showFullLogs)}
                        >
                          {showFullLogs ? 'Show less' : 'Show full logs'}
                        </Button>
                      )}
                    </div>
                    <ScrollArea className="h-96 w-full rounded-md border border-border bg-muted/60">
                      <pre
                        data-testid="task-logs"
                        className="p-4 text-xs font-mono whitespace-pre-wrap break-all text-foreground/90"
                      >
                        {taskDetails.logs}
                      </pre>
                    </ScrollArea>
                  </div>
                )}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Destructive-action confirmation (delete one / delete selected / cleanup) */}
      <DestructiveConfirmDialog
        open={confirmState !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmState(null);
        }}
        title={
          confirmState?.kind === 'cleanup'
            ? 'Clean up old operations'
            : confirmState?.kind === 'delete-bulk'
            ? 'Delete selected operations'
            : 'Delete operation'
        }
        description={
          confirmState?.kind === 'cleanup'
            ? 'Permanently delete finished operations older than 60 days. This cannot be undone.'
            : confirmState?.kind === 'delete-bulk'
            ? `Permanently delete ${confirmState.taskIds.length} selected operation(s). Running operations are skipped. This cannot be undone.`
            : 'Permanently delete this operation from the log. This cannot be undone.'
        }
        confirmText="DELETE"
        onConfirm={runConfirmedAction}
        isPending={confirmPending}
        consequences={['Operation history and logs will be removed from the database.']}
      />
    </div>
  );
}
