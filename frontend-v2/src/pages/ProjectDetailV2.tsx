/**
 * Project Detail Page V2 - Mission Control Layout
 *
 * Key features:
 * - Full-width module list with slide-over Sheet for module details
 * - Horizontal dependency pipeline visualization
 * - Clickable status badges show state info popover
 * - Inline module actions (Init, Plan, Apply)
 * - Detail Sheet with tabs (Outputs, State, Variables, Logs)
 *
 * Sub-components extracted to ./project-detail/:
 *   CollapsibleSection, StateInfoPopover, ModuleTableRow,
 *   ModuleGroupTable, StatusConfig
 */

import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useState, useMemo, useCallback, useEffect, useRef, lazy, Suspense } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ResourceViewTabs, type ResourceViewTab } from '@/components/layout/ResourceViewTabs';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
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
  ArrowLeft, Settings, Trash2, Cloud, Box,
  Loader2, Package, Zap, GitCompare,
  Terminal, FileOutput, Rocket,
  Database, Plus, GitBranch, ChevronDown,
  CloudCog, Shield, Server, Layers, Camera, UserCheck, AlertTriangle, Search, CheckCircle, CircuitBoard, Cpu,
  Clock, FileText,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { notify } from '@/lib/notify';
import { useAuthStore } from '@/stores/authStore';
import { useProject, useDeleteProject } from '@/hooks/useProjects';
import {
  useProjectModules,
  useInitModule,
  usePlanModule,
  useApplyModule,
  useDestroyModule,
  useDeleteModule,
  useRecoverState,
  useRerunModule,
  useCancelDeployment,
} from '@/hooks/useModules';
import { AddModuleToProjectDialog } from '@/components/modules/AddModuleToProjectDialog';
import { EditProjectDialog } from '@/components/projects/EditProjectDialog';
import { CloudCredentialsDialog } from '@/components/projects/CloudCredentialsDialog';
import { ProjectDependenciesDialog } from '@/components/projects/ProjectDependenciesDialog';
import { ProjectVariablesEditor } from '@/components/projects/ProjectVariablesEditor';
import { DriftSettings } from '@/components/drift/DriftSettings';
import { DriftCheckHistory } from '@/components/drift/DriftCheckHistory';
import { SnapshotHistory } from '@/components/snapshots/SnapshotHistory';
import { K8sClusterList } from '@/components/k8s/K8sClusterList';
import { SSHConnectivityBadge } from '@/components/ui/SSHConnectivityBadge';
import { ClusterStatusBadge } from '@/components/ui/ClusterStatusBadge';
import { useProjectClusters } from '@/hooks/useK8s';
import { ApplyConfirmDialog } from '@/components/modules/ApplyConfirmDialog';
import { EditModuleVariablesDialog } from '@/components/modules/EditModuleVariablesDialog';
import { ChangeModuleVersionDialog } from '@/components/modules/ChangeModuleVersionDialog';
import { RunModuleActionDialog } from '@/components/modules/RunModuleActionDialog';
import { SaveAsTemplateDialog } from '@/components/stacks/SaveAsTemplateDialog';
import { TransferOwnershipDialog } from '@/components/projects/TransferOwnershipDialog';
import { useStackInstances } from '@/hooks/useStacks';
import { ProjectSecretsManager } from '@/components/secrets/ProjectSecretsManager';
import { useRequiredSecrets } from '@/hooks/useProjectSecrets';
import { ParallelExecutionDialog } from '@/components/execution/ParallelExecutionDialog';
import { LogViewerModal } from '@/components/deployments/LogViewerModal';
// Lazy-load DeploymentPipeline — it pulls in ReactFlow (~143KB) and is only
// shown when the user switches to pipeline view during active deployments
const DeploymentPipeline = lazy(() =>
  import('@/components/deployments/DeploymentPipeline').then(m => ({ default: m.DeploymentPipeline }))
);
const BareMetalPanel = lazy(() =>
  import('@/components/bare-metal/BareMetalPanel').then((m) => ({ default: m.BareMetalPanel }))
);
const DpuPanel = lazy(() =>
  import('@/components/dpu/DpuPanel').then((m) => ({ default: m.DpuPanel }))
);
import { useActiveRunHandle, useRunProgress, useExecutionPlan } from '@/hooks/useParallelExecution';
import { getCloudProviderBadgeInfo, getProjectLocationInfo } from '@/lib/aws-regions';
import { NodeDiscoveryPanel } from '@/components/discovery/NodeDiscoveryPanel';
import { BackendBadge } from '@/components/projects/BackendBadge';
import type { ProjectModule } from '@/types';
import {
  getManagementBoundaryLabel,
  getPlatformProfileLabel,
  getProjectClusterPlatformMismatch,
  getProjectTargetPlatform,
} from '@/lib/platform-context';
import { isActionSupported } from '@/lib/module-engine';
import { formatModuleVariableValue, getSensitiveModuleVariableNames } from '@/lib/module-variables';

// Extracted sub-components
import { ModuleStateHistoryTab } from '@/components/modules/ModuleStateHistoryTab';
import { ModuleReportsTab } from '@/components/modules/ModuleReportsTab';

import { StateInfoPopover } from './project-detail/StateInfoPopover';
import { ModuleGroupTable } from './project-detail/ModuleGroupTable';

export default function ProjectDetailV2() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const projectId = parseInt(id || '0');

  const currentUser = useAuthStore((s) => s.user);
  const { data: project, isLoading: projectLoading } = useProject(projectId);
  // K8S-UX-001: clusters hook must be above projectMode which depends on it
  const { data: clusters } = useProjectClusters(projectId, { pollingEnabled: false });
  const locationInfo = project ? getProjectLocationInfo(project.cloud_provider, project.region, project.credential_template?.provider, project.project_type) : null;
  const providerBadge = project ? getCloudProviderBadgeInfo(project.cloud_provider || project.project_type) : null;
  const targetPlatform = getProjectTargetPlatform(project);
  const targetPlatformLabel = getPlatformProfileLabel(targetPlatform);
  const managementBoundaryLabel = getManagementBoundaryLabel(project?.management_boundary);
  const mismatchedClusters = (clusters || []).filter((cluster) =>
    getProjectClusterPlatformMismatch(project, cluster),
  );
  // MU-008: Ownership check — admin or project owner can mutate
  const isOwner = currentUser?.role === 'admin' || !project?.user_id || project?.user_id === currentUser?.id;
  const { data: modulesData, isLoading: modulesLoading } = useProjectModules(projectId, { pollingEnabled: true });
  const deleteProject = useDeleteProject();
  // D-001 P3: run-handle based parallel execution tracking
  const { activeRunHandle, activeAction, hasActiveRun, setActiveRunHandle, clearActiveRunHandle } = useActiveRunHandle();
  const { data: executionPlan } = useExecutionPlan(projectId, (modulesData?.length || 0) > 0);
  const { data: executionStatus } = useRunProgress(projectId, activeRunHandle);

  // Clear the active run handle once the run reaches a terminal state so buttons re-enable
  useEffect(() => {
    if (executionStatus && (executionStatus.status === 'completed' || executionStatus.status === 'failed')) {
      // Keep handle for 5s so the UI can show the final result before resetting
      const timer = setTimeout(() => clearActiveRunHandle(), 5000);
      return () => clearTimeout(timer);
    }
  }, [executionStatus, clearActiveRunHandle]);

  // Alias to D-020 naming used in JSX
  const hasActiveExecution = hasActiveRun;
  const activeExecution = activeAction ? { action: activeAction } : null;

  const [selectedModule, setSelectedModule] = useState<number | null>(null);
  const [detailTab, setDetailTab] = useState('outputs');
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [showAddModuleDialog, setShowAddModuleDialog] = useState(false);
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [showCredentialsDialog, setShowCredentialsDialog] = useState(false);
  const [showDependenciesDialog, setShowDependenciesDialog] = useState(false);
  const [showSaveAsTemplateDialog, setShowSaveAsTemplateDialog] = useState(false);
  const [showTransferDialog, setShowTransferDialog] = useState(false);
  const [applyConfirmOpen, setApplyConfirmOpen] = useState(false);
  const [editVariablesOpen, setEditVariablesOpen] = useState(false);
  const [changeVersionOpen, setChangeVersionOpen] = useState(false);
  const [runActionOpen, setRunActionOpen] = useState(false);
  const [destroyDialogOpen, setDestroyDialogOpen] = useState(false);
  const [deleteModuleDialogOpen, setDeleteModuleDialogOpen] = useState(false);
  const [recoverStateDialogOpen, setRecoverStateDialogOpen] = useState(false);
  const [moduleToAction, setModuleToAction] = useState<ProjectModule | null>(null);
  const [showParallelDeployDialog, setShowParallelDeployDialog] = useState(false);
  const [showParallelDestroyDialog, setShowParallelDestroyDialog] = useState(false);
  const [secretsPanelOpened, setSecretsPanelOpened] = useState(false);
  const [logsDialogOpen, setLogsDialogOpen] = useState(false);
  // K8S-UX-001: Detect project "mode" for adaptive layout
  // - k8s-only: has clusters but zero IaC modules → K8s operations focus
  // - iac: has IaC modules but zero clusters → infrastructure focus
  // - hybrid: has both → show everything
  // NOTE: Must be computed before defaultTab which depends on projectMode
  const projectMode = useMemo(() => {
    const hasModules = (modulesData || []).length > 0;
    const hasClusters = (clusters?.length || 0) > 0;
    if (hasClusters && !hasModules) return 'k8s-only' as const;
    if (hasModules && !hasClusters) return 'iac' as const;
    return 'hybrid' as const;
  }, [modulesData, clusters?.length]);

  // K8S-UX-002: Determine if cloud credentials are relevant
  const needsCloudCredentials = useMemo(() => {
    const isCloudProject = project?.cloud_provider && !['on-prem', 'none', null].includes(project.cloud_provider);
    return isCloudProject || (modulesData || []).length > 0;
  }, [project?.cloud_provider, modulesData]);

  // IMP-018: Sync page tab with URL query parameter for deep-linking & back/forward navigation
  const [searchParams, setSearchParams] = useSearchParams();
  const validTabs = ['modules', 'variables', 'secrets', 'clusters', 'discovery', 'drift', 'snapshots', 'bare-metal', 'dpu'] as const;
  type PageTab = typeof validTabs[number];
  const urlTab = searchParams.get('tab');
  // K8S-UX-001: Default tab depends on project mode — K8s-only projects
  // land on the clusters tab since IaC modules are irrelevant for them
  const defaultTab: PageTab = projectMode === 'k8s-only' ? 'clusters' : 'modules';
  const pageTab: PageTab = urlTab && (validTabs as readonly string[]).includes(urlTab)
    ? (urlTab as PageTab)
    : defaultTab;
  const setPageTab = useCallback((tab: PageTab) => {
    setSearchParams(prev => {
      if (tab === defaultTab) {
        prev.delete('tab');
      } else {
        prev.set('tab', tab);
      }
      return prev;
    }, { replace: true });
  }, [setSearchParams, defaultTab]);
  const [pipelineCollapsed, setPipelineCollapsed] = useState(() => {
    const stored = localStorage.getItem(`bnk-forge:project-${projectId}:pipeline-collapsed`);
    return stored !== null ? stored === 'true' : false;
  });
  // Resizable pipeline height state — null means use component's smart default
  const [pipelineHeight, setPipelineHeight] = useState<number | null>(null);
  const pipelineContainerRef = useRef<HTMLDivElement>(null);

  const modules = useMemo(() => modulesData || [], [modulesData]);
  const anyApplyUnsupported = useMemo(
    () => modules.some((module) => !isActionSupported(module, 'apply')),
    [modules],
  );
  const anyDestroyUnsupported = useMemo(
    () => modules.some((module) => !isActionSupported(module, 'destroy')),
    [modules],
  );
  const selectedModuleData = modules.find((m: ProjectModule) => m.id === selectedModule);
  const selectedModuleSensitiveVariableNames = useMemo(
    () => getSensitiveModuleVariableNames(selectedModuleData?.library_module),
    [selectedModuleData?.library_module]
  );

  const { data: stackInstances } = useStackInstances(projectId);
  const stackNameMap = useMemo(() =>
    (stackInstances || []).reduce((acc, stack) => {
      acc[stack.id] = stack.name;
      return acc;
    }, {} as Record<number, string>),
    [stackInstances]
  );

  // Derive the active stack's template slug for required-secrets lookup
  const activeStackSlug = useMemo(() => {
    const active = (stackInstances || []).find(
      (s) => s.status !== 'destroyed'
    );
    return active?.template_slug ?? undefined;
  }, [stackInstances]);

  const { data: stateInfo } = useQuery({
    queryKey: ['module-state-info', selectedModule],
    queryFn: () => selectedModule ? api.getModuleStateInfo(selectedModule) : null,
    enabled: !!selectedModule,
    staleTime: 5 * 60 * 1000,
  });

   // Track which task is expanded to fetch its full logs
  const [expandedTaskId, setExpandedTaskId] = useState<number | null>(null);
  const { data: expandedTaskDetail } = useQuery({
    queryKey: ['task-detail', expandedTaskId],
    queryFn: () => expandedTaskId ? api.getTask(expandedTaskId) : null,
    enabled: !!expandedTaskId,
  });

  // For pre-applied modules (BM-019 skip), find the source module's tasks
  // by looking for a sibling module with the same library path that has tasks.
  // source_path is the stable module identity across stacks (e.g. "bare-metal/probe-dpu").
  // Pick the oldest sibling (lowest id) so we surface logs from the first real run.
  const fallbackModuleId = useMemo(() => {
    if (!selectedModuleData || selectedModuleData.status !== 'applied') return null;
    // Use source_path (stable cross-stack identity) with fallback to path field
    const libPath = selectedModuleData.library_module?.source_path
      ?? selectedModuleData.library_module?.path;
    if (!libPath) return null;
    // Collect all siblings with matching source_path/path and applied status
    const siblings = modules.filter(
      (m: ProjectModule) => m.id !== selectedModuleData.id
        && (m.library_module?.source_path === libPath || m.library_module?.path === libPath)
        && m.status === 'applied'
    );
    if (siblings.length === 0) return null;
    // Prefer the oldest sibling (lowest id) — first real run is most likely to have task logs
    const oldest = siblings.reduce((a: ProjectModule, b: ProjectModule) => a.id < b.id ? a : b);
    return oldest.id;
  }, [selectedModuleData, modules]);

  const primaryTaskModuleId = selectedModule;
  const { data: moduleTasks } = useQuery({
    queryKey: ['module-tasks', primaryTaskModuleId],
    queryFn: () => primaryTaskModuleId ? api.getTasks({ module_id: primaryTaskModuleId }).then((res) => res.tasks) : null,
    enabled: !!primaryTaskModuleId,
    refetchInterval: detailTab === 'logs' ? 3000 : false,
  });

  // Fallback: if primary has no tasks and we have a sibling from a previous stack, fetch its tasks
  const { data: fallbackTasks } = useQuery({
    queryKey: ['module-tasks-fallback', fallbackModuleId],
    queryFn: () => fallbackModuleId ? api.getTasks({ module_id: fallbackModuleId }).then((res) => res.tasks) : null,
    enabled: !!fallbackModuleId && (!moduleTasks || moduleTasks.length === 0),
  });

  const effectiveTasks = (moduleTasks && moduleTasks.length > 0) ? moduleTasks : fallbackTasks;

  const { data: secretsRequired } = useRequiredSecrets(projectId, {
    stackSlug: activeStackSlug,
    enabled: secretsPanelOpened || pageTab === 'secrets',
  });

  useEffect(() => {
    if (pageTab === 'secrets') setSecretsPanelOpened(true);
  }, [pageTab]);

  // Module mutation hooks
  const initMutation = useInitModule();
  const planMutation = usePlanModule();
  const applyMutation = useApplyModule();
  const destroyMutation = useDestroyModule();
  const deleteModuleMutation = useDeleteModule();
  const recoverStateMutation = useRecoverState();
  const rerunMutation = useRerunModule();
  const cancelMutation = useCancelDeployment();

  const mutations = useMemo(() => ({
    initMutation, planMutation, applyMutation,
  }), [initMutation, planMutation, applyMutation]);

  const handleModuleAction = useCallback((module: ProjectModule, action: string) => {
    setModuleToAction(module);
    switch (action) {
      case 'init': initMutation.mutate(module.id); break;
      case 'plan': planMutation.mutate(module.id); break;
      case 'apply': setApplyConfirmOpen(true); break;
      case 'rerun': rerunMutation.mutate(module.id); break;
      case 'edit': setEditVariablesOpen(true); break;
      case 'change-version': setChangeVersionOpen(true); break;
      case 'cancel': cancelMutation.mutate(module.id); break;
      case 'run-action': setRunActionOpen(true); break;
      case 'recover': setRecoverStateDialogOpen(true); break;
      case 'destroy': setDestroyDialogOpen(true); break;
      case 'delete': setDeleteModuleDialogOpen(true); break;
    }
  }, [initMutation, planMutation, rerunMutation, cancelMutation]);

  const handleApplyConfirm = (autoApprove: boolean) => {
    if (moduleToAction) applyMutation.mutate({ moduleId: moduleToAction.id, autoApprove, engineType: moduleToAction.engine_type });
    setApplyConfirmOpen(false);
  };

  const handleDestroy = () => {
    if (moduleToAction) destroyMutation.mutate({ moduleId: moduleToAction.id, autoApprove: true });
    setDestroyDialogOpen(false);
  };

  const handleDeleteModule = () => {
    if (moduleToAction) deleteModuleMutation.mutate(moduleToAction.id);
    setDeleteModuleDialogOpen(false);
  };

  const handleRecoverState = () => {
    if (moduleToAction) recoverStateMutation.mutate(moduleToAction.id);
    setRecoverStateDialogOpen(false);
  };

  // Calculate stats
  const deployedCount = modules.filter((m: ProjectModule) => m.status === 'applied').length;
  const healthPercent = modules.length > 0 ? Math.round((deployedCount / modules.length) * 100) : 0;

  // Quiet single-line stat summary in header
  const quietStatLine = useMemo(() => {
    const parts: string[] = [];
    if (clusters?.length) parts.push(`${clusters.length} cluster${clusters.length === 1 ? '' : 's'}`);
    if (projectMode !== 'k8s-only') {
      parts.push(`${modules.length} module${modules.length === 1 ? '' : 's'}`);
      parts.push(`health ${healthPercent}%`);
      parts.push('0 drift');
    }
    return parts.join(' · ');
  }, [clusters?.length, projectMode, modules.length, healthPercent]);

  const { standaloneModules, stackGroups } = useMemo(() => {
    const standalone = modules.filter((m: ProjectModule) => !m.stack_instance_id);
    const stackMods = modules.filter((m: ProjectModule) => m.stack_instance_id);
    const groups = stackMods.reduce((acc: Record<number, ProjectModule[]>, m: ProjectModule) => {
      const stackId = m.stack_instance_id!;
      if (!acc[stackId]) acc[stackId] = [];
      acc[stackId].push(m);
      return acc;
    }, {});
    return { standaloneModules: standalone, stackGroups: groups };
  }, [modules]);

  const [isDeleting, setIsDeleting] = useState(false);
  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await deleteProject.mutateAsync(projectId);
      // Brief pause so user sees "Done" before navigating away
      await new Promise((r) => setTimeout(r, 800));
      navigate('/projects');
    } catch {
      // useDeleteProject() is a useAppMutation, whose default onError already
      // runs the error through notify/parseApiError. Raising a second, fixed
      // string here produced a duplicate toast that also masked the server's
      // text — including the 409 from the undestroyed-modules guard, which
      // names the blocking modules and the force=true override.
      setIsDeleting(false);
    }
  };

  if (projectLoading || modulesLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-32 w-full rounded-xl" />
        <Skeleton className="h-96 w-full rounded-xl" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="text-center py-12">
        <Box className="h-16 w-16 mx-auto mb-4 text-muted-foreground" />
        <h3 className="text-lg font-semibold mb-2">Project not found</h3>
        <Button onClick={() => navigate('/projects')}>Back to Projects</Button>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      {/* Header */}
      <div className="border-b border-border px-6 py-4 bg-card">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1">
              <Button variant="ghost" size="sm" onClick={() => navigate('/projects')} className="h-7 -ml-2">
                <ArrowLeft className="h-4 w-4 mr-1" />
                Back
              </Button>
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-foreground">{project.name}</h1>
            {project.description && (
              <p className="text-sm text-muted-foreground mt-1">{project.description}</p>
            )}
            <div className="flex flex-wrap items-center gap-2 mt-3">
              {providerBadge && (
                <Badge variant={providerBadge.badgeVariant} className={providerBadge.badgeClass}>
                  {providerBadge.shortLabel}
                </Badge>
              )}
              {locationInfo && (
                <Badge variant="outline" className="text-xs font-normal whitespace-nowrap gap-1">
                  <span>{locationInfo.flag}</span>
                  <span>{locationInfo.display}</span>
                </Badge>
              )}
              <BackendBadge
                backendType={project.backend_type}
                stateStorageProvider={project.state_storage_provider}
                s3Bucket={project.s3_state_bucket}
                s3Region={project.s3_state_region}
                useNativeLocking={project.s3_use_native_locking}
                s3Endpoint={project.s3_endpoint}
              />
              <Badge variant="outline" className="text-xs font-normal whitespace-nowrap">
                Target Platform: {targetPlatformLabel}
              </Badge>
              {managementBoundaryLabel && (
                <Badge variant="outline" className="text-xs font-normal whitespace-nowrap">
                  {managementBoundaryLabel}
                </Badge>
              )}
              {project.owner_username && (
                <Badge variant="outline" className="text-xs font-normal gap-1">
                  <Shield className="h-3 w-3" />
                  {project.owner_username}
                </Badge>
              )}
              {!isOwner && (
                <Badge variant="warning" className="text-xs">
                  View only
                </Badge>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2 mt-3">
              {quietStatLine && (
                <p className="text-sm text-muted-foreground">{quietStatLine}</p>
              )}
              {anyDestroyUnsupported && (
                <Badge variant="warning" className="text-xs">
                  Some modules don&apos;t support destroy
                </Badge>
              )}
              {project?.ssh_credential && (
                <SSHConnectivityBadge
                  credentialId={project.ssh_credential_id}
                  label={project.ssh_credential.name}
                  base={`${project.ssh_credential.username}@${project.ssh_credential.host}`}
                />
              )}
              {(clusters?.length ?? 0) > 0 && (clusters ?? []).map((c) => (
                <ClusterStatusBadge key={`reach-${c.id}`} cluster={c} />
              ))}
            </div>
          </div>
          {isOwner && (
            <div className="flex items-center gap-2 shrink-0">
              {modules.length > 0 && (
                <Button
                  variant="default"
                  size="sm"
                  onClick={() => setShowParallelDeployDialog(true)}
                  disabled={hasActiveExecution || anyApplyUnsupported}
                  title={
                    hasActiveExecution
                      ? `${activeExecution?.action} in progress`
                      : anyApplyUnsupported
                        ? 'One or more modules do not support apply'
                        : undefined
                  }
                >
                  {hasActiveExecution && activeExecution?.action === 'deploy' ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <Rocket className="h-4 w-4 mr-1.5" />}
                  Deploy all
                </Button>
              )}
              {projectMode !== 'k8s-only' && (
                <Button variant="outline" size="sm" onClick={() => setShowAddModuleDialog(true)}>
                  <Plus className="h-4 w-4 mr-1.5" />Add module
                </Button>
              )}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm">
                    More <ChevronDown className="h-4 w-4 ml-1" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {needsCloudCredentials && (
                    <DropdownMenuItem onSelect={() => setShowCredentialsDialog(true)}>
                      <Cloud className="h-4 w-4 mr-2" />Credentials
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onSelect={() => setShowEditDialog(true)}>
                    <Settings className="h-4 w-4 mr-2" />Settings
                  </DropdownMenuItem>
                  {modules.length > 0 && (
                    <DropdownMenuItem onSelect={() => setShowSaveAsTemplateDialog(true)}>
                      <Layers className="h-4 w-4 mr-2" />Save as template
                    </DropdownMenuItem>
                  )}
                  {modules.length > 0 && (
                    <DropdownMenuItem
                      onSelect={() => setShowParallelDestroyDialog(true)}
                      disabled={hasActiveExecution || anyDestroyUnsupported}
                      className="text-warning"
                    >
                      {hasActiveExecution && activeExecution?.action === 'destroy' ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Zap className="h-4 w-4 mr-2" />}
                      Destroy all
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onSelect={() => setShowTransferDialog(true)}>
                    <UserCheck className="h-4 w-4 mr-2" />Transfer ownership
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => setShowDeleteDialog(true)} className="text-destructive">
                    <Trash2 className="h-4 w-4 mr-2" />Delete project
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}
        </div>
      </div>

      {/* Page-level Tab Bar — K8S-UX-001: adaptive tab order based on project mode.
          Shared ResourceViewTabs idiom (D-020), identical to every other page. */}
      {(() => {
        const clusterBadge =
          clusters && clusters.length > 0 ? (
            <Badge variant={pageTab === 'clusters' ? 'info' : 'secondary'} className="text-xs h-5 px-2 font-medium">
              {clusters.length}
            </Badge>
          ) : undefined;
        const pageTabs: ResourceViewTab[] = [
          // K8s-only projects: Clusters tab first
          ...(projectMode === 'k8s-only'
            ? [{ key: 'clusters', label: 'K8s Clusters', icon: Server, badge: clusterBadge }]
            : []),
          {
            key: 'modules',
            label: projectMode === 'k8s-only' ? 'Modules' : 'Modules & Blueprints',
            icon: Layers,
            badge:
              modules.length > 0 ? (
                <Badge variant={pageTab === 'modules' ? 'info' : 'secondary'} className="text-xs h-5 px-2 font-medium">
                  {modules.length}
                </Badge>
              ) : undefined,
          },
          ...(projectMode !== 'k8s-only'
            ? [{ key: 'variables', label: 'Variables', icon: GitBranch }]
            : []),
          {
            key: 'secrets',
            label: 'Secrets',
            icon: Shield,
            badge: secretsRequired ? (
              <Badge
                variant={secretsRequired.missing_count > 0 ? 'destructive' : pageTab === 'secrets' ? 'info' : 'secondary'}
                className="text-xs h-5 px-2 font-medium"
              >
                {secretsRequired.missing_count > 0 ? `${secretsRequired.missing_count} missing` : `${secretsRequired.total_required}`}
              </Badge>
            ) : undefined,
          },
          ...(projectMode !== 'k8s-only'
            ? [{ key: 'clusters', label: 'K8s Clusters', icon: Server, badge: clusterBadge }]
            : []),
          { key: 'discovery', label: 'Discovery', icon: Search },
          ...(project?.project_type === 'bare-metal'
            ? [{ key: 'bare-metal', label: 'Bare Metal', icon: CircuitBoard }]
            : []),
          ...(project?.project_type === 'kubernetes' || project?.project_type === 'bare-metal'
            ? [{ key: 'dpu', label: 'DPU', icon: Cpu }]
            : []),
          ...(projectMode !== 'k8s-only'
            ? [
                { key: 'drift', label: 'Drift', icon: GitCompare },
                { key: 'snapshots', label: 'Snapshots', icon: Camera },
              ]
            : []),
        ];
        return (
          <ResourceViewTabs
            variant="strip"
            aria-label="Project sections"
            active={pageTab}
            onChange={(v) => setPageTab(v as PageTab)}
            tabs={pageTabs}
          />
        );
      })()}

      {/* ===== TAB CONTENT ===== */}

      {/* Secrets Tab */}
      {pageTab === 'secrets' && (
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="max-w-7xl">
            <ProjectSecretsManager projectId={projectId} requiredSecrets={secretsRequired?.required_secrets} />
          </div>
        </div>
      )}

      {/* K8s Clusters Tab */}
      {pageTab === 'clusters' && (
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="max-w-7xl space-y-6">
            {mismatchedClusters.length > 0 && (
              <div className="p-4 rounded-lg border border-l-2 border-l-warning border-border bg-card">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="h-4 w-4 text-warning mt-0.5" />
                  <div>
                    <h3 className="text-sm font-semibold text-warning">Platform mismatch detected</h3>
                    <p className="text-xs mt-1 text-foreground/80">
                      Project target is <span className="font-medium">{targetPlatformLabel}</span>, but {mismatchedClusters.length} cluster{mismatchedClusters.length > 1 ? 's are' : ' is'} detected on a different platform.
                      Runtime actions use detected cluster platform context.
                    </p>
                  </div>
                </div>
              </div>
            )}
            <K8sClusterList
              projectId={projectId}
              cloudProvider={project?.cloud_provider}
              targetPlatformProfile={project?.target_platform_profile}
              projectSshCredentialId={project?.ssh_credential_id}
            />
            {clusters && clusters.length > 0 && (
              <div className="p-4 rounded-lg border border-border bg-muted/50">
                <h3 className="text-sm font-semibold mb-2 text-foreground">View Resources</h3>
                <p className="text-sm mb-4 text-muted-foreground">
                  View and manage Kubernetes resources (pods, deployments, services, etc.) on the dedicated K8s page.
                </p>
                <Button variant="outline" size="sm" onClick={() => navigate(`/kubernetes?project=${projectId}`)}>
                  <CloudCog className="h-4 w-4 mr-1.5" />Go to Kubernetes page
                </Button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Discovery Tab */}
      {pageTab === 'discovery' && (
        <div className="flex-1 overflow-y-auto px-6 py-6">
            <div className="max-w-7xl">
              <NodeDiscoveryPanel
                projectId={projectId}
                canTrigger={isOwner}
                jumphost={project?.ssh_credential ?? null}
                projectSshCredentialId={project?.ssh_credential_id ?? undefined}
              />
            </div>
          </div>
      )}

      {/* Bare Metal Tab */}
      {pageTab === 'bare-metal' && (
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="max-w-7xl">
            <Suspense fallback={<Skeleton className="h-96 w-full" />}>
              <BareMetalPanel projectId={projectId} isOwner={isOwner} />
            </Suspense>
          </div>
        </div>
      )}

      {/* DPU Tab (Kubernetes and Bare Metal projects) */}
      {pageTab === 'dpu' && (
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="max-w-7xl">
            <Suspense fallback={<Skeleton className="h-96 w-full" />}>
              <DpuPanel projectId={projectId} isOwner={isOwner} />
            </Suspense>
          </div>
        </div>
      )}

      {/* Drift Tab */}
      {pageTab === 'drift' && (
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="max-w-7xl space-y-6">
            <DriftSettings projectId={projectId} />
            <DriftCheckHistory projectId={projectId} />
          </div>
        </div>
      )}

      {/* Snapshots Tab */}
      {pageTab === 'snapshots' && (
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="max-w-7xl">
            <SnapshotHistory projectId={projectId} />
          </div>
        </div>
      )}

      {/* Modules & Blueprints Tab (default) */}
      {pageTab === 'modules' && (
        <>
          {/* Dependency Pipeline */}
          {modules.length > 0 && (
            <div className="border-b border-border px-6 bg-card">
              <div className="flex items-center justify-between py-2">
                <button
                  onClick={() => {
                    setPipelineCollapsed(prev => {
                      const next = !prev;
                      localStorage.setItem(`bnk-forge:project-${projectId}:pipeline-collapsed`, String(next));
                      return next;
                    });
                  }}
                  className="flex items-center gap-2 text-xs font-medium transition-colors text-muted-foreground hover:text-foreground"
                >
                  <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', pipelineCollapsed && '-rotate-90')} />
                  Dependency Pipeline
                </button>
              </div>
              {!pipelineCollapsed && (
                <div className="pb-1">
                  <div ref={pipelineContainerRef}>
                    <Suspense fallback={<div className="flex items-center justify-center py-8"><Loader2 className="h-6 w-6 animate-spin text-primary" /><span className="ml-2 text-sm text-muted-foreground">Loading pipeline view...</span></div>}>
                      <DeploymentPipeline
                        executionPlan={executionPlan}
                        executionStatus={executionStatus}
                        modules={modules}
                        containerHeight={pipelineHeight ?? undefined}
                      />
                    </Suspense>
                  </div>
                  {/* Resize handle */}
                  <div
                    className="h-2 cursor-row-resize flex items-center justify-center group"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      const startY = e.clientY;
                      const startHeight = pipelineContainerRef.current?.offsetHeight || 300;
                      const onMove = (ev: MouseEvent) => {
                        setPipelineHeight(Math.max(150, Math.min(window.innerHeight * 0.8, startHeight + ev.clientY - startY)));
                      };
                      const onUp = () => {
                        document.removeEventListener('mousemove', onMove);
                        document.removeEventListener('mouseup', onUp);
                      };
                      document.addEventListener('mousemove', onMove);
                      document.addEventListener('mouseup', onUp);
                    }}
                  >
                    <div className="w-12 h-1 rounded-full bg-border group-hover:bg-primary transition-colors" />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Main Content: Full-width Module List */}
          <div className="flex-1 overflow-y-auto">
              {modules.length === 0 ? (
                <div className="flex items-center justify-center h-full">
                  <div className="text-center p-12">
                    {projectMode === 'k8s-only' ? (
                      <>
                        <Server className="h-16 w-16 mx-auto mb-4 text-muted-foreground" />
                        <h3 className="text-lg font-semibold mb-2 text-muted-foreground">K8s Operations Project</h3>
                        <p className="text-sm mb-4 text-muted-foreground">This project manages Kubernetes clusters. Add IaC modules here if you need infrastructure provisioning.</p>
                        {isOwner && <Button variant="outline" onClick={() => setShowAddModuleDialog(true)}><Plus className="h-4 w-4 mr-2" />Add Module (Optional)</Button>}
                      </>
                    ) : (
                      <>
                        <Package className="h-16 w-16 mx-auto mb-4 text-muted-foreground" />
                        <h3 className="text-lg font-semibold mb-2 text-muted-foreground">No modules yet</h3>
                        <p className="text-sm mb-4 text-muted-foreground">{isOwner ? 'Add your first module to get started' : 'No modules have been added to this project'}</p>
                        {isOwner && <Button variant="outline" size="sm" onClick={() => setShowAddModuleDialog(true)}><Plus className="h-4 w-4 mr-1.5" />Add module</Button>}
                      </>
                    )}
                  </div>
                </div>
              ) : (
                <div className="p-4 space-y-4" data-onboarding="module-list">
                  {standaloneModules.length > 0 && (
                    <ModuleGroupTable
                      title="Standalone Modules"
                      icon={<Package className="h-4 w-4 text-muted-foreground" />}
                      count={standaloneModules.length}
                      modules={standaloneModules}
                      allModules={modules}
                      selectedModule={selectedModule}
                      onSelectModule={setSelectedModule}
                      onModuleAction={handleModuleAction}
                      mutations={mutations}
                      moduleToAction={moduleToAction}
                      readOnly={!isOwner}
                    />
                  )}
                  {Object.entries(stackGroups).map(([stackIdStr, stackModulesList]) => {
                    const stackId = parseInt(stackIdStr);
                    const stackName = stackNameMap[stackId] || `Blueprint #${stackId}`;
                    const stackInst = stackInstances?.find(s => s.id === stackId);
                    const sortedStackModules = (stackModulesList as ProjectModule[]).sort((a, b) => a.deployment_order - b.deployment_order);
                    const isDeployed = stackInst?.status === 'deployed';
                    return (
                      <ModuleGroupTable
                        key={stackId}
                        title={stackName}
                        icon={<Layers className="h-4 w-4 text-info" />}
                        count={sortedStackModules.length}
                        modules={sortedStackModules}
                        allModules={modules}
                        selectedModule={selectedModule}
                        onSelectModule={setSelectedModule}
                        onModuleAction={handleModuleAction}
                        mutations={mutations}
                        moduleToAction={moduleToAction}
                        isStack
                        stackInstance={stackInst}
                        projectId={projectId}
                        collapsible
                        defaultCollapsed={isDeployed}
                        storageKey={`bnk-forge:project-${projectId}:stack-${stackId}:collapsed`}
                        readOnly={!isOwner}
                      />
                    );
                  })}
                </div>
              )}
          </div>

          {/* Module Detail Sheet — slides in from the right */}
          <Sheet open={selectedModule !== null} onOpenChange={(open) => { if (!open) setSelectedModule(null); }}>
            <SheetContent side="right" className="!w-[600px] sm:!w-[700px] !max-w-[50vw] overflow-y-auto p-0 bg-card border-border">
              {selectedModuleData && (
                <>
                  <SheetHeader className="p-4 pb-0">
                    <div className="flex items-start justify-between pr-8">
                      <div className="flex items-center gap-3">
                        <div className="h-10 w-10 rounded-lg flex items-center justify-center bg-primary/10">
                          <Package className="h-5 w-5 text-primary" />
                        </div>
                        <div>
                          <SheetTitle className="text-left">{selectedModuleData.module_name}</SheetTitle>
                          <p className="text-xs text-muted-foreground">{selectedModuleData.library_module?.category || 'Unknown category'}</p>
                        </div>
                      </div>
                      <StateInfoPopover module={selectedModuleData} />
                    </div>
                  </SheetHeader>
                  <div className="p-4">
                    <Tabs value={detailTab} onValueChange={setDetailTab} className="w-full">
                      {/* Compact shadcn tabs here on purpose — this is a tight
                          sub-control inside the module detail sheet, not page
                          navigation; the full-width ResourceViewTabs strip reads
                          as chunky buttons in this cramped context. */}
                      <TabsList className="grid w-full grid-cols-6">
                        <TabsTrigger value="outputs"><FileOutput className="h-4 w-4 mr-2" />Outputs</TabsTrigger>
                        <TabsTrigger value="state"><Database className="h-4 w-4 mr-2" />State</TabsTrigger>
                        <TabsTrigger value="variables"><GitBranch className="h-4 w-4 mr-2" />Variables</TabsTrigger>
                        <TabsTrigger value="logs"><Terminal className="h-4 w-4 mr-2" />Logs</TabsTrigger>
                        <TabsTrigger value="reports"><FileText className="h-4 w-4 mr-2" />Reports</TabsTrigger>
                        <TabsTrigger value="history"><Clock className="h-4 w-4 mr-2" />History</TabsTrigger>
                      </TabsList>

                      <TabsContent value="outputs" className="mt-4">
                        <div className="p-4 rounded-lg bg-muted/50">
                          <h4 className="text-sm font-semibold mb-3">Module Outputs</h4>
                          {selectedModuleData.outputs && Object.keys(selectedModuleData.outputs).length > 0 ? (
                            <div className="space-y-2">
                              {Object.entries(selectedModuleData.outputs).map(([key, value]) => (
                                <div key={key} className="flex justify-between items-start text-sm">
                                  <code className="font-mono text-xs text-muted-foreground">{key}:</code>
                                  <code className="font-mono text-xs ml-4 text-foreground/80">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</code>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <p className="text-sm text-center py-6 text-muted-foreground">No outputs available yet</p>
                          )}
                        </div>
                      </TabsContent>

                      <TabsContent value="state" className="mt-4">
                        <div className="p-4 rounded-lg bg-muted/50">
                          <h4 className="text-sm font-semibold mb-3">State Resources</h4>
                          {stateInfo?.state_exists ? (
                            <div className="space-y-3">
                              <div className="p-3 rounded text-sm bg-muted text-foreground/80">
                                <p className="font-medium mb-2">State Summary</p>
                                <div className="space-y-1 text-xs">
                                  <div>Resources: {stateInfo.resources_count || 0} managed</div>
                                  <div>Serial: {stateInfo.serial || 0}</div>
                                  {stateInfo.terraform_version && <div>Terraform: {stateInfo.terraform_version}</div>}
                                  {stateInfo.state_modified_at && <div>Modified: {new Date(stateInfo.state_modified_at).toLocaleString()}</div>}
                                </div>
                              </div>
                              <div className="p-2 rounded text-xs font-mono break-all bg-muted text-muted-foreground">{stateInfo.state_location}</div>
                            </div>
                          ) : (
                            <div className="text-center py-8">
                              <Database className="h-12 w-12 mx-auto mb-2 text-muted-foreground" />
                              <p className="text-sm text-muted-foreground">{stateInfo?.error || 'No state file exists yet'}</p>
                              <p className="text-xs mt-1 text-muted-foreground">Deploy this module to generate state</p>
                            </div>
                          )}
                        </div>
                      </TabsContent>

                      <TabsContent value="variables" className="mt-4">
                        <div className="p-4 rounded-lg bg-muted/50">
                          <h4 className="text-sm font-semibold mb-3">Module Variables</h4>
                          {selectedModuleData.variable_overrides && Object.keys(selectedModuleData.variable_overrides).length > 0 ? (
                            <div className="space-y-2">
                              {Object.entries(selectedModuleData.variable_overrides).map(([key, value]) => (
                                <div key={key} className="flex justify-between items-start p-2 rounded text-sm bg-muted">
                                  <code className="font-mono text-xs text-muted-foreground">{key}:</code>
                                  <code className="font-mono text-xs ml-4 break-all text-foreground/80">
                                    {formatModuleVariableValue(key, value, selectedModuleSensitiveVariableNames)}
                                  </code>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className="text-center py-8">
                              <GitBranch className="h-12 w-12 mx-auto mb-2 text-muted-foreground" />
                              <p className="text-sm text-muted-foreground">No variable overrides</p>
                              <p className="text-xs mt-1 text-muted-foreground">Using default values from module</p>
                            </div>
                          )}
                        </div>
                      </TabsContent>

                      <TabsContent value="logs" className="mt-4">
                        <div className="p-4 rounded-lg bg-muted/50">
                          <div className="flex items-center justify-between mb-4">
                            <h4 className="text-sm font-semibold">Operation Log</h4>
                            <Button variant="outline" size="sm" onClick={() => setLogsDialogOpen(true)}>
                              <Terminal className="h-3.5 w-3.5 mr-1.5" />Full Log Viewer
                            </Button>
                          </div>
                          {effectiveTasks && Array.isArray(effectiveTasks) && effectiveTasks.length > 0 ? (
                            <div className="space-y-2 max-h-[60vh] overflow-y-auto">
                              {effectiveTasks.slice().reverse().map((task: { id: number; task_type?: string; status?: string; command?: string; logs?: string; error?: string; created_at?: string; duration_seconds?: number }) => {
                                const isSsh = task.command?.startsWith('ssh-engine');
                                const label = isSsh
                                  ? (task.task_type === 'apply' ? 'Run' : task.task_type === 'init' ? 'Init' : task.task_type || 'Task')
                                  : (task.task_type || 'Task');
                                 const isExpanded = expandedTaskId === task.id;
                                const taskLogs = isExpanded ? expandedTaskDetail?.logs : null;
                                const taskError = isExpanded ? (expandedTaskDetail?.error || task.error) : null;
                                return (
                                  <div
                                    key={task.id}
                                    className={cn('rounded-lg border p-3 cursor-pointer transition-colors bg-card', isExpanded ? 'border-primary/50' : 'border-border')}
                                    onClick={() => setExpandedTaskId(isExpanded ? null : task.id)}
                                  >
                                    <div className="flex items-center justify-between mb-1">
                                      <div className="flex items-center gap-2">
                                        <Badge variant={task.status === 'completed' ? 'default' : task.status === 'failed' ? 'destructive' : 'secondary'} className="text-xs">
                                          {label}
                                        </Badge>
                                        <span className="text-xs text-muted-foreground">
                                          #{task.id}
                                        </span>
                                      </div>
                                      <div className="flex items-center gap-2">
                                        <span className="text-xs text-muted-foreground">
                                          {task.duration_seconds ? `${task.duration_seconds.toFixed(1)}s` : 'running...'}
                                        </span>
                                        <ChevronDown className={cn('h-3 w-3 transition-transform text-muted-foreground', isExpanded && 'rotate-180')} />
                                      </div>
                                    </div>
                                    {isExpanded && taskError && !taskLogs && (
                                      <div className="text-xs mt-2 p-2 rounded border border-l-2 border-l-destructive border-border bg-card text-destructive">
                                        <span className="font-semibold">Error: </span>{taskError}
                                      </div>
                                    )}
                                    {isExpanded && (taskLogs || !taskError) && (
                                      <pre className="text-xs font-mono mt-2 p-2 rounded max-h-[50vh] overflow-y-auto whitespace-pre-wrap break-all bg-muted text-foreground/80">
                                        {taskLogs || (expandedTaskDetail ? '(no logs)' : 'Loading...')}
                                      </pre>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          ) : (
                            <div className="text-center py-8">
                              <Terminal className="h-12 w-12 mx-auto mb-2 text-muted-foreground" />
                              <p className="text-sm text-muted-foreground">No operations recorded yet</p>
                              <p className="text-xs mt-1 text-muted-foreground">Run Init or Run to see operation logs here</p>
                            </div>
                          )}
                        </div>
                      </TabsContent>

                      <TabsContent value="reports" className="mt-4">
                        {/* Lazy: only fetch the report tree once the tab is opened. */}
                        {detailTab === 'reports' && (
                          <ModuleReportsTab moduleId={selectedModuleData.id} />
                        )}
                      </TabsContent>

                      <TabsContent value="history" className="mt-4">
                        <ModuleStateHistoryTab
                          moduleId={selectedModuleData.id}
                          currentStatus={selectedModuleData.status}
                        />
                      </TabsContent>
                    </Tabs>
                  </div>
                </>
              )}
            </SheetContent>
          </Sheet>

        </>
      )}

      {/* Variables Tab — moved out of Modules body */}
      {pageTab === 'variables' && projectMode !== 'k8s-only' && (
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="max-w-7xl">
            <p className="text-sm text-muted-foreground mb-4">
              Variables injected into all module deployments. Values from deployed
              infrastructure are auto-discovered.
            </p>
            <ProjectVariablesEditor projectId={projectId} />
          </div>
        </div>
      )}

      {/* Dialogs */}
      <AddModuleToProjectDialog module={null} projectId={projectId} open={showAddModuleDialog} onOpenChange={setShowAddModuleDialog} />
      <EditProjectDialog project={project} open={showEditDialog} onOpenChange={setShowEditDialog} />
      <CloudCredentialsDialog project={project} open={showCredentialsDialog} onOpenChange={setShowCredentialsDialog} />
      <ProjectDependenciesDialog project={project} onUpdate={() => {}} open={showDependenciesDialog} onOpenChange={setShowDependenciesDialog} />
      <SaveAsTemplateDialog projectId={project.id} projectName={project.name} moduleCount={modules.length} open={showSaveAsTemplateDialog} onOpenChange={setShowSaveAsTemplateDialog} onSuccess={() => notify.success('Template saved successfully!')} />
      <ParallelExecutionDialog open={showParallelDeployDialog} onOpenChange={setShowParallelDeployDialog} projectId={projectId} action="deploy" onExecutionStarted={(runHandle) => { setActiveRunHandle(runHandle, 'deploy'); notify.success('Parallel deployment started!', undefined, { category: 'deployment' }); setShowParallelDeployDialog(false); }} />
      <ParallelExecutionDialog open={showParallelDestroyDialog} onOpenChange={setShowParallelDestroyDialog} projectId={projectId} action="destroy" onExecutionStarted={(runHandle) => { setActiveRunHandle(runHandle, 'destroy'); notify.success('Parallel destruction started!', undefined, { category: 'deployment' }); setShowParallelDestroyDialog(false); }} />
      <TransferOwnershipDialog open={showTransferDialog} onOpenChange={setShowTransferDialog} project={project} />

      <AlertDialog open={showDeleteDialog} onOpenChange={(open) => { if (!isDeleting) setShowDeleteDialog(open); }}>
        <AlertDialogContent>
          {isDeleting ? (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle className="flex items-center gap-2">
                  <Loader2 className="h-5 w-5 animate-spin text-destructive" />
                  Deleting &quot;{project.name}&quot;...
                </AlertDialogTitle>
              </AlertDialogHeader>
              <div className="space-y-2 py-2">
                {[
                  { label: 'Kubernetes clusters', count: clusters?.length || 0 },
                  { label: 'Modules', count: project.module_count || 0 },
                  { label: 'Secrets & credentials', count: null },
                  { label: 'Deployment history', count: (project.deployed_count || 0) + (project.failed_count || 0) },
                  { label: 'Project configuration', count: null },
                ].filter((item) => item.count === null || item.count > 0).map((item) => (
                  <div key={item.label} className="flex items-center gap-2 text-sm text-muted-foreground">
                    {deleteProject.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
                    ) : (
                      <CheckCircle className="h-3.5 w-3.5 text-success shrink-0" />
                    )}
                    <span>
                      {item.label}
                      {item.count != null && <span className="ml-1 text-xs opacity-60">({item.count})</span>}
                    </span>
                  </div>
                ))}
              </div>
              {!deleteProject.isPending && (
                <div className="flex items-center gap-2 text-sm text-success font-medium pt-1">
                  <CheckCircle className="h-4 w-4" />
                  Done — redirecting...
                </div>
              )}
            </>
          ) : (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete Project</AlertDialogTitle>
                <AlertDialogDescription>
                  Are you sure you want to delete &quot;{project.name}&quot;? This will permanently remove:
                </AlertDialogDescription>
              </AlertDialogHeader>
              <ul className="space-y-1.5 text-sm text-muted-foreground py-1">
                {(clusters?.length || 0) > 0 && (
                  <li className="flex items-center gap-2"><Trash2 className="h-3.5 w-3.5 text-destructive shrink-0" />{clusters!.length} cluster{clusters!.length > 1 ? 's' : ''}</li>
                )}
                {project.module_count > 0 && (
                  <li className="flex items-center gap-2"><Trash2 className="h-3.5 w-3.5 text-destructive shrink-0" />{project.module_count} module{project.module_count > 1 ? 's' : ''}</li>
                )}
                <li className="flex items-center gap-2"><Trash2 className="h-3.5 w-3.5 text-destructive shrink-0" />Secrets, credentials & deployment history</li>
                <li className="flex items-center gap-2"><Trash2 className="h-3.5 w-3.5 text-destructive shrink-0" />Project configuration</li>
              </ul>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <Button variant="destructive" onClick={handleDelete}>Delete</Button>
              </AlertDialogFooter>
            </>
          )}
        </AlertDialogContent>
      </AlertDialog>

      {selectedModuleData && (
        <LogViewerModal
          open={logsDialogOpen}
          onOpenChange={setLogsDialogOpen}
          moduleName={selectedModuleData.module_name || 'Module'}
          moduleId={(!moduleTasks || moduleTasks.length === 0) && fallbackModuleId ? fallbackModuleId : selectedModuleData.id}
        />
      )}

      {moduleToAction && (
        <>
          <ApplyConfirmDialog open={applyConfirmOpen} onOpenChange={setApplyConfirmOpen} onConfirm={handleApplyConfirm} moduleName={moduleToAction.module_name} engineType={moduleToAction.engine_type} />
          <EditModuleVariablesDialog open={editVariablesOpen} onOpenChange={setEditVariablesOpen} module={moduleToAction} />
          {/* Mount only while open so opening other row actions doesn't trigger
              the dialog's module-library fetch (same pattern as ProjectModuleCard). */}
          {changeVersionOpen && (
            <ChangeModuleVersionDialog open={changeVersionOpen} onOpenChange={setChangeVersionOpen} module={moduleToAction} />
          )}
          {/* Mount only while open so opening other row actions doesn't trigger
              the dialog's module-actions fetch (same pattern as ProjectModuleCard). */}
          {runActionOpen && (
            <RunModuleActionDialog open={runActionOpen} onOpenChange={setRunActionOpen} module={moduleToAction} />
          )}
          <AlertDialog open={destroyDialogOpen} onOpenChange={setDestroyDialogOpen}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Destroy Infrastructure</AlertDialogTitle>
                <AlertDialogDescription>Are you sure you want to destroy all infrastructure managed by &quot;{moduleToAction.module_name}&quot;? This will delete all cloud resources created by this module.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={handleDestroy} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">Destroy</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <AlertDialog open={deleteModuleDialogOpen} onOpenChange={setDeleteModuleDialogOpen}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete Module</AlertDialogTitle>
                <AlertDialogDescription>Are you sure you want to delete the &quot;{moduleToAction.module_name}&quot; module from this project? This will NOT destroy cloud infrastructure.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={handleDeleteModule} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">Delete Module</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <AlertDialog open={recoverStateDialogOpen} onOpenChange={setRecoverStateDialogOpen}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Recover State File</AlertDialogTitle>
                <AlertDialogDescription>
                  This will reconnect to your existing infrastructure and rebuild the OpenTofu state file.
                  Use this when:
                  <ul className="list-disc list-inside mt-2 space-y-1">
                    <li>Infrastructure exists in AWS but state file was lost</li>
                    <li>State file became corrupted</li>
                    <li>You need to import existing infrastructure</li>
                  </ul>
                  <p className="mt-2 text-warning">Warning: This will query AWS for existing resources and rebuild state.</p>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={handleRecoverState} className="bg-primary text-primary-foreground hover:bg-primary/90">Recover State</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </>
      )}
    </div>
  );
}
