/**
 * Module Table Header and Row Components
 * Extracted from ProjectDetailV2 for readability.
 * ModuleTableRow is wrapped with React.memo to prevent unnecessary re-renders.
 */

import { memo, useState } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Settings, Trash2, XCircle, Loader2, MoreVertical,
  RefreshCcw, RefreshCw, AlertTriangle, Eye, Play, GitBranch, Tag, Ban, FlaskConical,
} from 'lucide-react';

// Statuses where a long-running operation is in flight and can be cancelled.
// Mirrors the in-progress states the backend cancel endpoint can unstick — the
// four transitional ModuleStatus values.
const RUNNING_STATUSES = [
  'initializing', 'planning', 'applying', 'destroying',
];

// Destroy is hidden only where no infrastructure can exist: the pristine pre-plan
// states (not_initialized, init_failed) and the already-torn-down state
// (destroyed). In every other non-running state (initialized, planned, plan_failed,
// applied, apply_failed, destroy_failed, failed) infra may exist and destroy —
// which is idempotent — must be offered so a partial/failed deploy can be torn
// down. This set is the exact complement of RUNNING_STATUSES + the backend
// submit_destroy allowlist (project_module_service.py), so every offered Destroy
// is accepted.
const DESTROY_HIDDEN_STATUSES = ['not_initialized', 'init_failed', 'destroyed'];
import { DISPLAY_LIMITS } from '@/lib/constants';
import { DependencyStatusBadge, checkDependenciesReady, getDependencyErrorMessage } from '@/components/modules/DependencyStatusBadge';
import { StateInfoPopover } from './StateInfoPopover';
import { notify } from '@/lib/notify';
import { useModuleActions } from '@/hooks/useModuleActions';
import type { ProjectModule } from '@/types';
import {
  getEnginePresentation,
  isActionSupported,
  resolveLifecycleCapabilities,
  resolveProjectModuleEngineType,
} from '@/lib/module-engine';

// Module Table Header - extracted for DRY
export function ModuleTableHeader() {
  return (
    <thead className="bg-muted/30">
      <tr className="text-xs text-muted-foreground">
        <th scope="col" className="text-left py-2 px-4 font-medium w-12">#</th>
        <th scope="col" className="text-left py-2 px-4 font-medium">Module</th>
        <th scope="col" className="text-left py-2 px-4 font-medium">Engine</th>
        <th scope="col" className="text-left py-2 px-4 font-medium">Status</th>
        <th scope="col" className="text-left py-2 px-4 font-medium">Dependencies</th>
        <th scope="col" className="text-right py-2 px-4 font-medium w-48">Actions</th>
      </tr>
    </thead>
  );
}

// Module Table Row Component
export const ModuleTableRow = memo(function ModuleTableRow({
  module,
  index,
  allModules,
  isSelected,
  onSelect,
  onAction,
  mutations,
  moduleToAction,
  readOnly = false,
}: {
  module: ProjectModule;
  index: number;
  allModules: ProjectModule[];
  isSelected: boolean;
  onSelect: () => void;
  onAction: (module: ProjectModule, action: string) => void;
  mutations: { initMutation: { isPending: boolean }; planMutation: { isPending: boolean }; applyMutation: { isPending: boolean } };
  moduleToAction: ProjectModule | null;
  /** MU-008: When true, hides all mutating actions (view-only for non-owners) */
  readOnly?: boolean;
}) {
  const { initMutation, planMutation, applyMutation } = mutations;
  const [menuOpen, setMenuOpen] = useState(false);
  // D-034: probe declared actions lazily — only while the menu is open on an
  // applied module (non-container modules return an empty list).
  const { data: moduleActions, isLoading: moduleActionsLoading } = useModuleActions(module.id, {
    enabled: menuOpen && module.status === 'applied',
  });
  const declaredActions = moduleActions?.actions ?? [];
  const resolvedEngineType = resolveProjectModuleEngineType(module);
  const capabilities = resolveLifecycleCapabilities(module);
  const enginePresentation = getEnginePresentation(
    resolvedEngineType,
    module.library_module?.module_type
  );
  const supportsInit = isActionSupported(module, 'init');
  const supportsPlan = isActionSupported(module, 'plan');
  const supportsApply = isActionSupported(module, 'apply');
  const supportsDestroy = isActionSupported(module, 'destroy');
  const isRunning = RUNNING_STATUSES.includes(module.status);

  return (
    <tr
      data-testid="module-card"
      className={cn(
        'border-t border-border hover:bg-muted/50 cursor-pointer transition-colors',
        isSelected && 'bg-primary/10'
      )}
      onClick={onSelect}
    >
      <td className="py-3 px-4">
        <span className="text-xs font-mono text-muted-foreground">
          {index + 1}
        </span>
      </td>
      <td className="py-3 px-4">
        <div>
          <div className="font-medium">{module.module_name}</div>
          <div className="text-xs text-muted-foreground">
            {module.library_module?.category || 'Unknown'}
          </div>
        </div>
      </td>
      <td className="py-3 px-4">
        <div className="flex items-center gap-1.5 flex-wrap">
          <Badge variant="outline" className={cn('text-[10px]', enginePresentation.className)}>
            {enginePresentation.label}
          </Badge>
          {!capabilities.supports_destroy && (
            <Badge variant="warning" className="text-[10px]">
              No destroy
            </Badge>
          )}
          {module.connectivity_risk && (
            <Badge variant="warning" className="text-[10px]">
              ⚠ SSH risk
            </Badge>
          )}
        </div>
      </td>
      <td className="py-3 px-4">
        <StateInfoPopover module={module} />
      </td>
      <td className="py-3 px-4">
        {module.dependencies && module.dependencies.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {module.dependencies.slice(0, DISPLAY_LIMITS.DEPENDENCY_PREVIEW).map((depId: number) => {
              const dep = allModules.find((m: ProjectModule) => m.id === depId);
              return (
                <DependencyStatusBadge
                  key={depId}
                  dependencyModule={dep ?? null}
                />
              );
            })}
            {module.dependencies.length > 3 && (
              <Badge variant="outline" className="text-[10px]">
                +{module.dependencies.length - 3}
              </Badge>
            )}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </td>
      <td className="py-3 px-4 text-right">
        {readOnly ? (
          <span className="text-xs text-muted-foreground">—</span>
        ) : (
        <div className="flex items-center justify-end" onClick={(e) => e.stopPropagation()}>
          <DropdownMenu onOpenChange={setMenuOpen}>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="h-7 w-7 p-0" aria-label="Module actions" data-testid="module-actions-menu">
                <MoreVertical className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {/* Same slide-in the row-click opens (Outputs / State / Variables /
                  Logs / Reports) — surfaced in the menu so it's discoverable. */}
              <DropdownMenuItem onClick={onSelect}>
                <Eye className="h-4 w-4 mr-2" />
                View Details
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              {isRunning && (
                <>
                  <DropdownMenuItem onClick={() => onAction(module, 'cancel')} className="text-destructive">
                    <Ban className="h-4 w-4 mr-2" />
                    Stop
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                </>
              )}
              {(module.status === 'not_initialized' || module.status === 'init_failed') && supportsInit ? (
                <DropdownMenuItem
                  onClick={() => onAction(module, 'init')}
                  disabled={initMutation.isPending && moduleToAction?.id === module.id}
                >
                  {initMutation.isPending && moduleToAction?.id === module.id ? (
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4 mr-2" />
                  )}
                  {module.status === 'init_failed' ? 'Retry Init' : 'Initialize'}
                </DropdownMenuItem>
              ) : supportsPlan || supportsApply ? (
                <>
                  {supportsPlan && resolvedEngineType !== 'ssh' && (
                    <DropdownMenuItem
                      onClick={() => onAction(module, 'plan')}
                      disabled={planMutation.isPending && moduleToAction?.id === module.id}
                    >
                      {planMutation.isPending && moduleToAction?.id === module.id ? (
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      ) : (
                        <Eye className="h-4 w-4 mr-2" />
                      )}
                      Plan
                    </DropdownMenuItem>
                  )}
                  {supportsApply && (
                    <DropdownMenuItem
                      onClick={() => {
                        const depsCheck = checkDependenciesReady(module, allModules);
                        if (!depsCheck.ready) {
                          const errorMsg = getDependencyErrorMessage(module, allModules);
                          if (errorMsg) {
                            notify.error('Dependencies Not Ready', errorMsg, { category: 'deployment' });
                          }
                          return;
                        }
                        onAction(module, 'apply');
                      }}
                      disabled={
                        (applyMutation.isPending && moduleToAction?.id === module.id) ||
                        !checkDependenciesReady(module, allModules).ready
                      }
                      className={cn(
                        !checkDependenciesReady(module, allModules).ready && 'opacity-50 cursor-not-allowed'
                      )}
                    >
                      {applyMutation.isPending && moduleToAction?.id === module.id ? (
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      ) : !checkDependenciesReady(module, allModules).ready ? (
                        <AlertTriangle className="h-4 w-4 mr-2" />
                      ) : (
                        <GitBranch className="h-4 w-4 mr-2" />
                      )}
                      {resolvedEngineType === 'ssh' ? 'Run' : 'Apply'}
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuSeparator />
                </>
              ) : null}
              {resolvedEngineType === 'ssh' && ['applied', 'deployed', 'apply_failed', 'init_failed'].includes(module.status) && (
                <DropdownMenuItem onClick={() => onAction(module, 'rerun')}>
                  <RefreshCw className="h-4 w-4 mr-2" />
                  Re-run
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => onAction(module, 'edit')}>
                <Settings className="h-4 w-4 mr-2" />
                Edit Variables
              </DropdownMenuItem>
              {module.library_module?.version && (
                <DropdownMenuItem onClick={() => onAction(module, 'change-version')}>
                  <Tag className="h-4 w-4 mr-2" />
                  Change Version
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              {module.status === 'applied' && (
                moduleActionsLoading ? (
                  <DropdownMenuItem disabled>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Run Action…
                  </DropdownMenuItem>
                ) : declaredActions.length > 0 ? (
                  <DropdownMenuItem onClick={() => onAction(module, 'run-action')}>
                    <FlaskConical className="h-4 w-4 mr-2" />
                    Run Action…
                  </DropdownMenuItem>
                ) : (
                  <DropdownMenuItem disabled title="No actions declared">
                    <FlaskConical className="h-4 w-4 mr-2" />
                    No actions declared
                  </DropdownMenuItem>
                )
              )}
              {(module.status === 'applied' || module.status === 'deployed') && (
                <DropdownMenuItem onClick={() => onAction(module, 'recover')} className="text-primary">
                  <RefreshCcw className="h-4 w-4 mr-2" />
                  Recover State
                </DropdownMenuItem>
              )}
              {supportsDestroy && !isRunning && !DESTROY_HIDDEN_STATUSES.includes(module.status) && (
                <DropdownMenuItem onClick={() => onAction(module, 'destroy')} className="text-destructive">
                  <XCircle className="h-4 w-4 mr-2" />
                  Destroy
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => onAction(module, 'delete')} className="text-destructive">
                <Trash2 className="h-4 w-4 mr-2" />
                Delete Module
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        )}
      </td>
    </tr>
  );
});
