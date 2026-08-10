import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import type { ProjectModule } from '@/types';
import {
  Package,
  MoreVertical,
  Play,
  GitBranch,
  Trash2,
  Settings,
  Eye,
  FileOutput,
  XCircle,
  Loader2,
  CheckCircle2,
  RefreshCcw,
  RefreshCw,
  ArrowUpCircle,
} from 'lucide-react';
import { ModuleStatusBadge } from '@/components/shared/ModuleStatusBadge';
import { useState } from 'react';
import { getCategoryColor } from '@/lib/categoryColors';
import {
  useInitModule,
  usePlanModule,
  useApplyModule,
  useDestroyModule,
  useDeleteModule,
  useCancelDeployment,
  useRecoverState,
  useRerunModule,
} from '@/hooks/useModules';
import { OpenTofuPlanDialog } from './OpenTofuPlanDialog';
import { EditModuleVariablesDialog } from './EditModuleVariablesDialog';
import { ChangeModuleVersionDialog } from './ChangeModuleVersionDialog';
import { SnapshotManager } from './SnapshotManager';
import { LogViewerModal } from '@/components/deployments/LogViewerModal';
import { ModuleOutputsViewer } from '@/components/deployments/ModuleOutputsViewer';
import { ApplyConfirmDialog } from './ApplyConfirmDialog';
import { DependencyStatusDisplay } from './DependencyStatusDisplay';
import { StateStatusIndicator } from './StateStatusIndicator';
import { VariableMappingsManager } from './VariableMappingsManager';
import { PlanStatusIndicator } from './PlanStatusIndicator';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
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

interface ProjectModuleCardProps {
  module: ProjectModule;
  allModules?: ProjectModule[];
}

export function ProjectModuleCard({ module, allModules = [] }: ProjectModuleCardProps) {
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [destroyDialogOpen, setDestroyDialogOpen] = useState(false);
  const [recoverStateDialogOpen, setRecoverStateDialogOpen] = useState(false);
  const [applyConfirmOpen, setApplyConfirmOpen] = useState(false);
  const [planDialogOpen, setPlanDialogOpen] = useState(false);
  const [editVariablesOpen, setEditVariablesOpen] = useState(false);
  const [changeVersionOpen, setChangeVersionOpen] = useState(false);
  const [snapshotsDialogOpen, setSnapshotsDialogOpen] = useState(false);
  const [logsDialogOpen, setLogsDialogOpen] = useState(false);
  const [outputsDialogOpen, setOutputsDialogOpen] = useState(false);
  const [variableMappingsOpen, setVariableMappingsOpen] = useState(false);

  const initMutation = useInitModule();
  const planMutation = usePlanModule();
  const applyMutation = useApplyModule();
  const destroyMutation = useDestroyModule();
  const deleteMutation = useDeleteModule();
  const cancelMutation = useCancelDeployment();
  const recoverStateMutation = useRecoverState();
  const rerunMutation = useRerunModule();

  const handleInit = () => {
    initMutation.mutate(module.id);
  };

  const handlePlan = () => {
    setPlanDialogOpen(true);
  };

  const handleApply = () => {
    setApplyConfirmOpen(true);
  };

  const handleApplyConfirm = (autoApprove: boolean) => {
    applyMutation.mutate({ moduleId: module.id, autoApprove, engineType: module.engine_type });
  };

  const handleDestroy = () => {
    destroyMutation.mutate({ moduleId: module.id, autoApprove: true });
    setDestroyDialogOpen(false);
  };

  const handleDelete = () => {
    deleteMutation.mutate(module.id);
    setDeleteDialogOpen(false);
  };

  const handleCancel = () => {
    cancelMutation.mutate(module.id);
  };

  const handleRecoverState = () => {
    recoverStateMutation.mutate(module.id);
    setRecoverStateDialogOpen(false);
  };

  const isDeploying =
    module.status === 'initializing' ||
    module.status === 'planning' ||
    module.status === 'applying' ||
    module.status === 'destroying';

  return (
    <>
      <Card data-testid="module-card" className="transition-all hover:shadow-md">
        <CardHeader>
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-lg ${getCategoryColor(module.category)} flex items-center justify-center`}>
                <Package className="h-5 w-5 text-white" />
              </div>
              <div className="flex-1">
                <CardTitle className="text-lg">{module.module_name || module.path_in_project.split('/').pop() || 'Module'}</CardTitle>
                <CardDescription className="text-xs mt-1">
                  {module.path_in_project}
                </CardDescription>
              </div>
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="Module actions">
                  <MoreVertical className="h-4 w-4" aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {(module.status === 'not_initialized' || module.status === 'init_failed') && (
                  <DropdownMenuItem onClick={handleInit}>
                    <Play className="h-4 w-4 mr-2" />
                    {module.status === 'init_failed' ? 'Retry Init' : 'Initialize'}
                  </DropdownMenuItem>
                )}
                <DropdownMenuItem onClick={() => setEditVariablesOpen(true)}>
                  <Settings className="h-4 w-4 mr-2" />
                  Edit Variables
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setVariableMappingsOpen(true)}>
                  <GitBranch className="h-4 w-4 mr-2" />
                  Variable Mappings
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setChangeVersionOpen(true)}>
                  <ArrowUpCircle className="h-4 w-4 mr-2" />
                  Change Version
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setOutputsDialogOpen(true)}>
                  <FileOutput className="h-4 w-4 mr-2" />
                  View Outputs
                </DropdownMenuItem>
                {/* Show Recover State button only for OpenTofu deployed modules (when state was lost) */}
                {module.engine_type !== 'ssh' && (module.status === 'applied' || module.status === 'deployed') && (
                  <DropdownMenuItem
                    onClick={() => setRecoverStateDialogOpen(true)}
                    className="text-info"
                  >
                    <RefreshCcw className="h-4 w-4 mr-2" />
                    Recover State
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                {module.engine_type !== 'ssh' && (
                  <DropdownMenuItem
                    onClick={() => setDestroyDialogOpen(true)}
                    className="text-warning"
                  >
                    <XCircle className="h-4 w-4 mr-2" />
                    Destroy
                  </DropdownMenuItem>
                )}
                <DropdownMenuItem
                  onClick={() => setDeleteDialogOpen(true)}
                  className="text-destructive"
                >
                  <Trash2 className="h-4 w-4 mr-2" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Status:</span>
              <div className="flex flex-col items-end">
                <div className="flex items-center gap-1.5">
                  <ModuleStatusBadge status={module.status} />
                  {module.connectivity_risk && (
                    <Badge variant="warning" className="text-[10px] ml-1">
                      Connectivity risk
                    </Badge>
                  )}
                </div>
                {isDeploying && module.stage_detail && (
                  <p className="text-xs text-muted-foreground mt-0.5 animate-pulse">
                    {module.stage_detail}
                  </p>
                )}
              </div>
            </div>

            {/* Deployed Status - Show when module is applied */}
            {(module.status === 'applied' || module.status === 'deployed') && module.last_deployed_at && (
              <div className="flex items-center gap-2 text-sm">
                <Badge variant="success" className="flex items-center gap-1">
                  <CheckCircle2 className="h-3 w-3" />
                  Deployed
                </Badge>
                <span className="text-muted-foreground">
                  {new Date(module.last_deployed_at).toLocaleString()}
                </span>
              </div>
            )}

            {module.library_module?.description && (
              <p className="text-sm text-muted-foreground line-clamp-2">{module.library_module.description}</p>
            )}

            {/* Plan Status Indicator - shows if saved plan exists (OpenTofu only) */}
            {module.engine_type !== 'ssh' && module.status !== 'not_initialized' && (
              <PlanStatusIndicator moduleId={module.id} />
            )}

            {/* State File Status */}
            <StateStatusIndicator module={module} />

            {/* Dependency Status */}
            {module.dependencies && module.dependencies.length > 0 && (
              <DependencyStatusDisplay module={module} allModules={allModules} />
            )}

            {/* Action Buttons */}
            <div className="flex flex-col gap-2 pt-2">
              {!module.status || module.status === 'not_initialized' || module.status === 'init_failed' ? (
                <Button
                  size="sm"
                  onClick={handleInit}
                  disabled={initMutation.isPending}
                  className="w-full"
                >
                  {initMutation.isPending ? (
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4 mr-2" />
                  )}
                  {initMutation.isPending ? 'Initializing...' : module.status === 'init_failed' ? 'Retry Init' : 'Initialize'}
                </Button>
              ) : (
                <>
                  {isDeploying ? (
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={handleCancel}
                      disabled={cancelMutation.isPending}
                      className="w-full"
                    >
                      <XCircle className="h-4 w-4 mr-2" />
                      Cancel Deployment
                    </Button>
                  ) : module.engine_type === 'ssh' ? (
                    <div className="flex flex-col gap-2">
                      <Button
                        size="sm"
                        onClick={handleApply}
                        disabled={applyMutation.isPending}
                        className="w-full"
                      >
                        {applyMutation.isPending ? (
                          <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        ) : (
                          <Play className="h-4 w-4 mr-2" />
                        )}
                        {applyMutation.isPending ? 'Running...' : 'Run'}
                      </Button>
                      {['applied', 'deployed', 'apply_failed', 'init_failed'].includes(module.status) && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => rerunMutation.mutate(module.id)}
                          disabled={rerunMutation.isPending}
                          className="w-full"
                        >
                          {rerunMutation.isPending ? (
                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                          ) : (
                            <RefreshCw className="h-4 w-4 mr-2" />
                          )}
                          {rerunMutation.isPending ? 'Re-running...' : 'Re-run'}
                        </Button>
                      )}
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={handlePlan}
                        disabled={planMutation.isPending}
                      >
                        {planMutation.isPending ? (
                          <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        ) : (
                          <Eye className="h-4 w-4 mr-2" />
                        )}
                        {planMutation.isPending ? 'Planning...' : 'Plan'}
                      </Button>
                      <Button
                        size="sm"
                        onClick={handleApply}
                        disabled={applyMutation.isPending}
                      >
                        {applyMutation.isPending ? (
                          <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        ) : (
                          <GitBranch className="h-4 w-4 mr-2" />
                        )}
                        {applyMutation.isPending ? 'Applying...' : 'Apply'}
                      </Button>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* OpenTofu Plan Dialog (OpenTofu only — not applicable to SSH modules) */}
      {module.engine_type !== 'ssh' && (
        <OpenTofuPlanDialog
          open={planDialogOpen}
          onOpenChange={setPlanDialogOpen}
          module={module}
        />
      )}

      {/* Apply Confirmation Dialog */}
      <ApplyConfirmDialog
        open={applyConfirmOpen}
        onOpenChange={setApplyConfirmOpen}
        moduleName={module.library_module?.name || 'this module'}
        onConfirm={handleApplyConfirm}
        isPending={applyMutation.isPending}
        engineType={module.engine_type}
      />

      {/* Edit Variables Dialog */}
      <EditModuleVariablesDialog
        open={editVariablesOpen}
        onOpenChange={setEditVariablesOpen}
        module={module}
      />

      {/* Change Version Dialog (D-033) — mounted only while open: it queries the
          module library for this path's versions, which is wasted work (and an
          unnecessary dependency for every card's test) when closed. */}
      {changeVersionOpen && (
        <ChangeModuleVersionDialog
          open={changeVersionOpen}
          onOpenChange={setChangeVersionOpen}
          module={module}
        />
      )}

      {/* Snapshots & Rollback Dialog */}
      <Dialog open={snapshotsDialogOpen} onOpenChange={setSnapshotsDialogOpen}>
        <DialogContent className="max-w-5xl max-h-[90vh] overflow-hidden">
          <DialogHeader>
            <DialogTitle>Snapshots & Rollback</DialogTitle>
            <DialogDescription>
              Manage snapshots and rollback to previous states for {module.library_module?.name || 'this module'}
            </DialogDescription>
          </DialogHeader>
          <SnapshotManager module={module} />
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Module</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete "{module.module_name}"? This will only remove the module
              configuration. Infrastructure will not be destroyed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete}>Delete Module</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Destroy Confirmation */}
      <AlertDialog open={destroyDialogOpen} onOpenChange={setDestroyDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Destroy Infrastructure</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to destroy all infrastructure for "{module.library_module?.name || 'this module'}"? This will
              delete all cloud resources created by this module. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDestroy}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Destroy Infrastructure
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Recover State Confirmation */}
      <AlertDialog open={recoverStateDialogOpen} onOpenChange={setRecoverStateDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Recover State File</AlertDialogTitle>
            <AlertDialogDescription>
              This will reconnect to your existing infrastructure and rebuild the OpenTofu state file.
              Use this when:
              <ul className="list-disc list-inside mt-2 space-y-1">
                <li>Infrastructure exists in AWS but state file was lost</li>
                <li>Module was deployed before state persistence was enabled</li>
              </ul>
              <div className="mt-3 p-2 bg-warning/10 rounded border border-warning/20">
                <strong className="text-warning">Important:</strong>
                <span className="text-warning"> Only use this if infrastructure exists in AWS. This will run `tofu refresh` to rebuild state from existing resources.</span>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleRecoverState}>
              Recover State
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Log Viewer Modal */}
      <LogViewerModal
        open={logsDialogOpen}
        onOpenChange={setLogsDialogOpen}
        moduleName={module.module_name || module.path_in_project.split('/').pop() || 'Module'}
        moduleId={module.id}
      />

      {/* Module Outputs Dialog */}
      <Dialog open={outputsDialogOpen} onOpenChange={setOutputsDialogOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Module Outputs</DialogTitle>
            <DialogDescription>
              {module.engine_type === 'ssh'
                ? `View outputs from ${module.library_module?.name || 'this module'}`
                : `View OpenTofu outputs for ${module.library_module?.name || 'this module'}`}
            </DialogDescription>
          </DialogHeader>
          <ModuleOutputsViewer module={module} />
        </DialogContent>
      </Dialog>

      {/* Variable Mappings Dialog */}
      <Dialog open={variableMappingsOpen} onOpenChange={setVariableMappingsOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Variable Mappings</DialogTitle>
            <DialogDescription>
              Configure variable name mappings and transformations for {module.library_module?.name || 'this module'}
            </DialogDescription>
          </DialogHeader>
          <VariableMappingsManager moduleId={module.id} />
        </DialogContent>
      </Dialog>
    </>
  );
}
