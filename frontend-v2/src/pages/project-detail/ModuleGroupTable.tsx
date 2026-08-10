/**
 * Module Group Table - with collapsible stack groups and integrated stack actions
 * Extracted from ProjectDetailV2 for readability.
 *
 * Phase grouping: when modules carry a `phase` field (from SSH engine metadata),
 * consecutive modules sharing a phase are rendered under a collapsible phase
 * header showing aggregate status and elapsed time.
 */

import { useState, useCallback, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Trash2, Loader2, ChevronDown, Rocket, MoreVertical, AlertCircle, Clock,
  CheckCircle2, XCircle,
} from 'lucide-react';
import { useDestroyStack, useRunStackDeployment } from '@/hooks/useStacks';
import { stackStatusConfig } from './StatusConfig';
import { ModuleTableHeader, ModuleTableRow } from './ModuleTableRow';
import { notify } from '@/lib/notify';
import type { ProjectModule } from '@/types';
import { isActionSupported, resolveProjectModuleEngineType } from '@/lib/module-engine';

// ---------------------------------------------------------------------------
// Phase grouping helpers
// ---------------------------------------------------------------------------

interface PhaseGroup {
  phase: string;
  modules: ProjectModule[];
}

type PhaseAggregateStatus = 'complete' | 'failed' | 'in_progress' | 'pending';

/** Group modules by phase preserving first-occurrence order. */
function groupModulesByPhase(modules: ProjectModule[]): PhaseGroup[] {
  const groups: PhaseGroup[] = [];
  for (const module of modules) {
    const phase = module.phase || '';
    const last = groups[groups.length - 1];
    if (last && last.phase === phase) {
      last.modules.push(module);
    } else {
      groups.push({ phase, modules: [module] });
    }
  }
  return groups;
}

/** Compute aggregate status for a set of modules. */
function getPhaseAggregateStatus(modules: ProjectModule[]): PhaseAggregateStatus {
  const statuses = modules.map(m => m.status);
  if (statuses.some(s => s === 'apply_failed' || s === 'init_failed' || s === 'failed')) return 'failed';
  if (statuses.some(s => s === 'applying' || s === 'initializing' || s === 'planning')) return 'in_progress';
  if (statuses.every(s => s === 'applied' || s === 'deployed')) return 'complete';
  return 'pending';
}

/** Wall-clock time for a phase: span from first to last module completion. */
function getPhaseElapsedSeconds(modules: ProjectModule[]): number {
  const deployTimes = modules
    .map(m => m.last_deployed_at ? new Date(m.last_deployed_at).getTime() : 0)
    .filter(t => t > 0);

  if (deployTimes.length >= 2) {
    const span = (Math.max(...deployTimes) - Math.min(...deployTimes)) / 1000;
    // Add the first module's own execution time (span starts at its completion)
    const firstModuleOutputs = modules[0]?.outputs as Record<string, unknown> | undefined;
    const firstDur = typeof firstModuleOutputs?.execution_duration_seconds === 'number'
      ? firstModuleOutputs.execution_duration_seconds : 0;
    return span + firstDur;
  }

  // Single module or no timestamps: use execution_duration_seconds
  return modules.reduce((total, m) => {
    const outputs = m.outputs as Record<string, unknown> | undefined;
    const dur = outputs?.execution_duration_seconds;
    return total + (typeof dur === 'number' ? dur : 0);
  }, 0);
}

/** Format seconds as "Xm Ys" or "Xs". */
function formatDuration(seconds: number): string {
  if (seconds < 1) return '';
  const rounded = Math.round(seconds);
  if (rounded < 60) return `${rounded}s`;
  const mins = Math.floor(rounded / 60);
  const secs = rounded % 60;
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
}

/** Whether any module in the list has a non-empty phase. */
function hasAnyPhase(modules: ProjectModule[]): boolean {
  return modules.some(m => !!m.phase);
}

export function ModuleGroupTable({
  title,
  icon,
  count,
  modules,
  allModules,
  selectedModule,
  onSelectModule,
  onModuleAction,
  mutations,
  moduleToAction,
  isStack = false,
  stackInstance,
  projectId,
  collapsible = false,
  defaultCollapsed = false,
  storageKey,
  readOnly = false,
}: {
  title: string;
  icon: React.ReactNode;
  count: number;
  modules: ProjectModule[];
  allModules: ProjectModule[];
  selectedModule: number | null;
  onSelectModule: (id: number) => void;
  onModuleAction: (module: ProjectModule, action: string) => void;
  mutations: { initMutation: { isPending: boolean }; planMutation: { isPending: boolean }; applyMutation: { isPending: boolean } };
  moduleToAction: ProjectModule | null;
  isStack?: boolean;
  stackInstance?: import('@/types').StackInstance;
  projectId?: number;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  storageKey?: string;
  /** MU-008: When true, hides all mutating actions (non-owner view) */
  readOnly?: boolean;
}) {
  // Persist collapse state in localStorage
  const [isCollapsed, setIsCollapsed] = useState(() => {
    if (!collapsible) return false;
    if (storageKey) {
      const stored = localStorage.getItem(storageKey);
      if (stored !== null) return stored === 'true';
    }
    return defaultCollapsed;
  });

  const toggleCollapsed = useCallback(() => {
    if (!collapsible) return;
    setIsCollapsed(prev => {
      const next = !prev;
      if (storageKey) localStorage.setItem(storageKey, String(next));
      return next;
    });
  }, [collapsible, storageKey]);

  // Phase grouping: only active when at least one module has a phase set
  const phaseGroups = useMemo(() => groupModulesByPhase(modules), [modules]);
  const showPhaseHeaders = useMemo(() => hasAnyPhase(modules), [modules]);
  const [collapsedPhases, setCollapsedPhases] = useState<Record<string, boolean>>({});

  const togglePhase = useCallback((phase: string) => {
    setCollapsedPhases(prev => ({ ...prev, [phase]: !prev[phase] }));
  }, []);

  // Stack action state.
  //
  // The stack_instance status only flips via the stack-level "Deploy All" flow,
  // so a per-module Apply/Destroy leaves it stale — e.g. the title shows
  // "Destroyed" while a module is mid-apply. Reconcile the DISPLAYED status with
  // live module activity so the badge matches the module rows. (Action gating
  // below still keys off the real stack_instance.status.)
  const effectiveStackStatus: string = (() => {
    if (modules.some(m => m.status === 'destroying')) return 'destroying';
    if (modules.some(m => ['applying', 'initializing', 'planning', 'deploying'].includes(m.status))) return 'deploying';
    if (modules.some(m => ['apply_failed', 'init_failed', 'failed', 'destroy_failed'].includes(m.status))) return 'failed';
    if (modules.length > 0 && modules.every(m => ['applied', 'deployed'].includes(m.status))) return 'deployed';
    return stackInstance?.status ?? 'pending';
  })();
  const stackStatus = stackInstance ? (stackStatusConfig[effectiveStackStatus] || stackStatusConfig.pending) : null;
  const StackStatusIcon = stackStatus?.icon || Clock;
  const isStackAnimated = ['deploying', 'destroying'].includes(effectiveStackStatus);
  const isStackInProgress = ['deploying', 'destroying'].includes(effectiveStackStatus);
  // Live phase to surface in the header while deploying — the stage_detail of
  // whichever module is currently mid-operation (e.g. "[3/6] deploy-prereqs").
  const currentPhaseDetail = modules.find(m =>
    ['applying', 'initializing', 'planning', 'destroying', 'deploying'].includes(m.status)
  )?.stage_detail || null;
  const stackProgress = stackInstance?.total_steps
    ? Math.round((stackInstance.current_step / (stackInstance.total_steps || 1)) * 100)
    : 0;

  // Stack action hooks — always called (React rules), but only used when stackInstance exists
  const destroyStackMutation = useDestroyStack(projectId || 0, stackInstance?.id || 0);
  const runDeployStackMutation = useRunStackDeployment(projectId || 0, stackInstance?.id || 0);
  const [stackDeleteDialogOpen, setStackDeleteDialogOpen] = useState(false);

  const hasDeployedInfra = stackInstance && ['deployed', 'failed'].includes(stackInstance.status) && (stackInstance.deployed_modules?.length || 0) > 0;
  // Only check infra modules for destroy support — SSH/ansible modules are
  // stateless (no remote state) and the backend skips them during destroy.
  // Mixed stacks (SSH + K8s/OpenTofu) should be destroyable if the infra
  // modules support it.
  const infraModules = modules.filter((module) => {
    const engine = resolveProjectModuleEngineType(module);
    return engine !== 'ssh' && engine !== 'ansible';
  });
  const stackSupportsDestroy = infraModules.length === 0 || infraModules.every((module) => isActionSupported(module, 'destroy'));
  const isAllStateless = infraModules.length === 0;
  // A blueprint is stuck when it's in a transitional state (deploying/destroying)
  // or has failed — stuck blueprints should always be deleteable via force-delete.
  const isStuckOrFailed = stackInstance && ['deploying', 'destroying', 'failed'].includes(stackInstance.status);
  const stackDestroyDisabledReason = hasDeployedInfra && !stackSupportsDestroy && !isAllStateless && !isStuckOrFailed
    ? 'One or more modules in this blueprint do not support destroy.'
    : null;
  const canDeployStack = stackInstance && ['pending', 'failed', 'deployed'].includes(stackInstance.status) && (stackInstance.deployed_modules?.length || 0) > 0;
  // Delete is always permitted when a stack instance exists. The backend reconciles
  // stale "deploying" status before enforcing guards; force=true is passed when the
  // stack is stuck/failed or all modules are stateless to bypass the destroy flow.

  const handleRunStackDeploy = async () => {
    try {
      await runDeployStackMutation.mutateAsync();
      notify.success('Blueprint deployment started', undefined, { category: 'deployment' });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Failed to deploy blueprint';
      notify.error(message, undefined, { category: 'deployment' });
    }
  };

  const handleDestroyStack = async () => {
    // Pass force=true when the stack is stuck/failed or all modules are stateless
    // so the backend bypasses the destroy flow and deletes directly.
    const force = !!isStuckOrFailed || isAllStateless;
    try {
      await destroyStackMutation.mutateAsync(force ? { force: true } : undefined);
      notify.success(force ? 'Blueprint deleted' : 'Blueprint destruction initiated', undefined, { category: 'deployment' });
      setStackDeleteDialogOpen(false);
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Failed to delete blueprint';
      notify.error(message, undefined, { category: 'deployment' });
    }
  };

  return (
    <>
      <div className="rounded-xl border border-border bg-card overflow-hidden">
        {/* Header */}
        <div
          className={cn(
            'px-4 py-2.5 border-b border-border flex items-center gap-2',
            isStack ? 'bg-info/10' : 'bg-muted/50',
            collapsible && 'cursor-pointer select-none hover:opacity-80 transition-opacity'
          )}
          onClick={collapsible ? toggleCollapsed : undefined}
        >
          {collapsible && (
            <ChevronDown className={cn(
              'h-4 w-4 transition-transform flex-shrink-0 text-muted-foreground',
              isCollapsed && '-rotate-90'
            )} />
          )}
          {icon}
          <span className="text-sm font-medium truncate">{title}</span>

          {stackInstance && stackStatus && (
            <Badge variant="outline" className={cn('text-xs flex-shrink-0', stackStatus.color)}>
              <StackStatusIcon className={cn('h-3 w-3 mr-1', isStackAnimated && 'animate-spin')} />
              {stackStatus.label}
            </Badge>
          )}

          {isStackInProgress && currentPhaseDetail && (
            <span className="text-xs font-medium text-info truncate animate-pulse" title={currentPhaseDetail}>
              {currentPhaseDetail}
            </span>
          )}

          {stackInstance && stackInstance.started_at && (
            <span className="text-xs text-muted-foreground">
              {stackInstance.completed_at
                ? formatDuration(
                    (new Date(stackInstance.completed_at).getTime() - new Date(stackInstance.started_at).getTime()) / 1000
                  )
                : `${formatDuration(
                    (Date.now() - new Date(stackInstance.started_at).getTime()) / 1000
                  )} elapsed`
              }
            </span>
          )}

          <div className="flex items-center gap-2 ml-auto flex-shrink-0">
            <Badge
              variant="outline"
              className={cn('text-xs', isStack && 'border-info/30 text-info')}
            >
              {count} modules
            </Badge>
            {stackInstance && !readOnly && (
              <div onClick={(e) => e.stopPropagation()}>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0" aria-label="Blueprint actions">
                      <MoreVertical className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {canDeployStack && (
                      <DropdownMenuItem
                        onClick={handleRunStackDeploy}
                        disabled={runDeployStackMutation.isPending || !!isStackInProgress}
                        className="text-success"
                      >
                        {runDeployStackMutation.isPending ? (
                          <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        ) : (
                          <Rocket className="h-4 w-4 mr-2" />
                        )}
                        {stackInstance.status === 'failed' ? 'Retry Deploy' : stackInstance.status === 'deployed' ? 'Redeploy Blueprint' : 'Deploy Blueprint'}
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuItem
                      className="text-destructive"
                      onClick={() => setStackDeleteDialogOpen(true)}
                      disabled={destroyStackMutation.isPending || (hasDeployedInfra && !stackSupportsDestroy && !isAllStateless && !isStuckOrFailed)}
                    >
                      <Trash2 className="h-4 w-4 mr-2" />
                      {isStuckOrFailed ? 'Force Delete Blueprint' : hasDeployedInfra && !isAllStateless ? (stackSupportsDestroy ? 'Destroy Blueprint' : 'Destroy Not Supported') : 'Delete Blueprint'}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            )}
          </div>
        </div>

        {stackDestroyDisabledReason && !isCollapsed && (
          <div className="px-4 py-2 border-b border-border" role="note">
            <div className="p-2 rounded text-xs bg-warning/10 text-warning border border-warning/20">
              <div className="flex items-start gap-2">
                <AlertCircle className="h-3 w-3 flex-shrink-0 mt-0.5" />
                <span>{stackDestroyDisabledReason}</span>
              </div>
            </div>
          </div>
        )}

        {/* Stack progress bar */}
        {stackInstance && isStackInProgress && stackInstance.total_steps && !isCollapsed && (
          <div className="px-4 py-2 border-b border-border" role="status" aria-live="polite" aria-label={`Deployment progress: step ${stackInstance.current_step} of ${stackInstance.total_steps}`}>
            <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
              <span>Step {stackInstance.current_step} of {stackInstance.total_steps}</span>
              <span>{stackProgress}%</span>
            </div>
            <Progress value={stackProgress} className="h-1.5" aria-label="Deployment progress" />
          </div>
        )}

        {/* Stack error message */}
        {stackInstance && stackInstance.status === 'failed' && stackInstance.error_message && !isCollapsed && (
          <div className="px-4 py-2 border-b border-border" role="alert">
            <div className="p-2 rounded text-xs bg-destructive/10 text-destructive border border-destructive/20">
              <div className="flex items-start gap-2">
                <AlertCircle className="h-3 w-3 flex-shrink-0 mt-0.5" />
                <span className="line-clamp-2">{stackInstance.error_message}</span>
              </div>
            </div>
          </div>
        )}

        {/* Module table — with optional phase grouping */}
        {!isCollapsed && (
          <table className="w-full text-sm">
            <ModuleTableHeader />
            <tbody>
              {showPhaseHeaders ? (
                (() => {
                  let runningIndex = 0;
                  return phaseGroups.map((group) => {
                    const startIndex = runningIndex;
                    runningIndex += group.modules.length;
                    const isPhaseCollapsed = !!collapsedPhases[group.phase];
                    return (
                      <PhaseGroupRows
                        key={`phase-${group.phase || '_ungrouped'}-${startIndex}`}
                        group={group}
                        startIndex={startIndex}
                        isPhaseCollapsed={isPhaseCollapsed}
                        onTogglePhase={togglePhase}
                        allModules={allModules}
                        selectedModule={selectedModule}
                        onSelectModule={onSelectModule}
                        onModuleAction={onModuleAction}
                        mutations={mutations}
                        moduleToAction={moduleToAction}
                        readOnly={readOnly}
                      />
                    );
                  });
                })()
              ) : (
                modules.map((module: ProjectModule, index: number) => (
                  <ModuleTableRow
                    key={module.id}
                    module={module}
                    index={index}
                    allModules={allModules}
                    isSelected={selectedModule === module.id}
                    onSelect={() => onSelectModule(module.id)}
                    onAction={onModuleAction}
                    mutations={mutations}
                    moduleToAction={moduleToAction}
                    readOnly={readOnly}
                  />
                ))
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* Stack Destroy Confirmation Dialog */}
      {stackInstance && (
        <AlertDialog open={stackDeleteDialogOpen} onOpenChange={setStackDeleteDialogOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                {isStuckOrFailed ? 'Force Delete Blueprint?' : hasDeployedInfra && !isAllStateless ? 'Destroy Blueprint?' : 'Delete Blueprint?'}
              </AlertDialogTitle>
              <AlertDialogDescription asChild>
                <div>
                  {isStuckOrFailed && (
                    <div className="mb-3 p-2 rounded text-xs bg-warning/10 text-warning border border-warning/20 flex items-start gap-2">
                      <AlertCircle className="h-3 w-3 flex-shrink-0 mt-0.5" />
                      <span>
                        This blueprint is in <strong>{stackInstance.status}</strong> state. Force-deleting will remove it from the database immediately — any in-progress operations will be abandoned.
                      </span>
                    </div>
                  )}
                  <span>
                    {hasDeployedInfra && !isAllStateless && !isStuckOrFailed
                      ? `This will destroy all ${stackInstance.deployed_modules?.length || 0} modules in "${stackInstance.name}" in reverse order. This action cannot be undone.`
                      : `This will remove "${stackInstance.name}" and its modules.${isAllStateless ? ' SSH/ansible modules have no remote state to clean up.' : ''}`
                    }
                  </span>
                </div>
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                onClick={handleDestroyStack}
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              >
                {destroyStackMutation.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    {isStuckOrFailed ? 'Force Deleting...' : hasDeployedInfra && !isAllStateless ? 'Destroying...' : 'Deleting...'}
                  </>
                ) : (
                  isStuckOrFailed ? 'Force Delete' : hasDeployedInfra && !isAllStateless ? 'Destroy Blueprint' : 'Delete Blueprint'
                )}
              </AlertDialogAction>
             </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Phase group sub-components (rendered inside <tbody>)
// ---------------------------------------------------------------------------

function PhaseStatusIndicator({ status }: { status: PhaseAggregateStatus }) {
  switch (status) {
    case 'complete':
      return <CheckCircle2 className="h-3.5 w-3.5 text-success" />;
    case 'failed':
      return <XCircle className="h-3.5 w-3.5 text-destructive" />;
    case 'in_progress':
      return <Loader2 className="h-3.5 w-3.5 text-info animate-spin" />;
    case 'pending':
    default:
      return <Clock className="h-3.5 w-3.5 text-muted-foreground" />;
  }
}

/** Renders a phase header row + the module rows for that phase group. */
function PhaseGroupRows({
  group,
  startIndex,
  isPhaseCollapsed,
  onTogglePhase,
  allModules,
  selectedModule,
  onSelectModule,
  onModuleAction,
  mutations,
  moduleToAction,
  readOnly,
}: {
  group: PhaseGroup;
  startIndex: number;
  isPhaseCollapsed: boolean;
  onTogglePhase: (phase: string) => void;
  allModules: ProjectModule[];
  selectedModule: number | null;
  onSelectModule: (id: number) => void;
  onModuleAction: (module: ProjectModule, action: string) => void;
  mutations: { initMutation: { isPending: boolean }; planMutation: { isPending: boolean }; applyMutation: { isPending: boolean } };
  moduleToAction: ProjectModule | null;
  readOnly: boolean;
}) {
  const aggregateStatus = getPhaseAggregateStatus(group.modules);
  const elapsed = getPhaseElapsedSeconds(group.modules);
  const formattedElapsed = formatDuration(elapsed);
  const hasHeader = !!group.phase;

  return (
    <>
      {/* Phase header row — only shown for named phases */}
      {hasHeader && (
        <tr
          className="cursor-pointer select-none transition-colors bg-muted/40 hover:bg-muted/60"
          onClick={() => onTogglePhase(group.phase)}
        >
          <td colSpan={6} className="py-1.5 px-4">
            <div className="flex items-center gap-2">
              <ChevronDown
                className={cn(
                  'h-3.5 w-3.5 transition-transform flex-shrink-0 text-muted-foreground',
                  isPhaseCollapsed && '-rotate-90',
                )}
              />
              <span className="text-xs font-medium text-muted-foreground">
                {group.phase}
              </span>
              <PhaseStatusIndicator status={aggregateStatus} />
              <span className="text-[10px] text-muted-foreground">
                {group.modules.length} {group.modules.length === 1 ? 'module' : 'modules'}
              </span>
              {aggregateStatus === 'complete' && formattedElapsed && (
                <span className="text-[10px] ml-auto text-muted-foreground">
                  {formattedElapsed}
                </span>
              )}
            </div>
          </td>
        </tr>
      )}
      {/* Module rows — hidden when phase is collapsed */}
      {(!hasHeader || !isPhaseCollapsed) &&
        group.modules.map((module, i) => (
          <ModuleTableRow
            key={module.id}
            module={module}
            index={startIndex + i}
            allModules={allModules}
            isSelected={selectedModule === module.id}
            onSelect={() => onSelectModule(module.id)}
            onAction={onModuleAction}
            mutations={mutations}
            moduleToAction={moduleToAction}
            readOnly={readOnly}
          />
        ))}
    </>
  );
}
