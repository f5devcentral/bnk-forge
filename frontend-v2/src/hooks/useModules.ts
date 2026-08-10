import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { POLL_INTERVALS } from '@/lib/constants';
import type {
  AddModuleRequest,
  ProjectModule,
  VariableMappingRequest,
  VariableMappingTemplateRequest,
} from '@/types';
import { useEffect, useRef } from 'react';
import { notify, deploymentNotify } from '@/lib/notify';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export function useModuleLibrary(params?: { category?: string; provider?: string; search?: string }) {
  return useQuery({
    queryKey: queryKeys.modules.library.list(params),
    queryFn: () => api.getModuleLibrary(params),
  });
}

export function useModuleDetails(moduleId: number) {
  return useQuery({
    queryKey: queryKeys.modules.library.detail(moduleId),
    queryFn: () => api.getModuleDetails(moduleId),
    enabled: !!moduleId,
  });
}

export function useModuleVariables(moduleId: number) {
  return useQuery({
    queryKey: queryKeys.modules.library.variables(moduleId),
    queryFn: () => api.getModuleVariables(moduleId),
    enabled: !!moduleId,
  });
}

export function useModuleCategories() {
  return useQuery({
    queryKey: queryKeys.modules.library.categories(),
    queryFn: api.getModuleCategories,
  });
}

export function useModuleProviders() {
  return useQuery({
    queryKey: queryKeys.modules.library.providers(),
    queryFn: api.getModuleProviders,
  });
}

export function useProjectModules(projectId: number, options?: { pollingEnabled?: boolean }) {
  const queryClient = useQueryClient();
  const previousStatusesRef = useRef<Map<number, string>>(new Map());
  // Track which transitions we've already notified to prevent duplicates
  const notifiedTransitionsRef = useRef<Set<string>>(new Set());

  const query = useQuery({
    queryKey: queryKeys.modules.project.byProject(projectId),
    queryFn: () => api.getProjectModules(projectId),
    enabled: !!projectId,
    // Smart polling: only poll frequently if there are active operations
    refetchInterval: options?.pollingEnabled ? (query) => {
      const data = query.state.data;
      // Poll every 3 seconds if any module has an active operation
      if (data && Array.isArray(data) && data.some((module: ProjectModule) =>
        ['initializing', 'planning', 'applying', 'destroying'].includes(module.status)
      )) {
        return 3000; // Fast polling during operations
      }
      // Otherwise poll every 15 seconds for status changes
      return 15000;
    } : false,
    // Keep previous data while refetching to prevent UI flicker
    placeholderData: (previousData) => previousData,
  });

  // Watch for status changes and invalidate project query when deployment operations complete
  useEffect(() => {
    if (!query.data || !Array.isArray(query.data)) return;

    const modules = query.data as ProjectModule[];
    const currentStatuses = new Map(modules.map(m => [m.id, m.status]));

    // Check if any module status changed from an active operation to a completed state
    let shouldInvalidateProject = false;

    modules.forEach(module => {
      const previousStatus = previousStatusesRef.current.get(module.id);
      const currentStatus = module.status;

      // If module transitioned from active operation to completed/failed state
      if (previousStatus && previousStatus !== currentStatus) {
        const wasActive = ['initializing', 'planning', 'applying', 'destroying'].includes(previousStatus);
        const isCompleted = ['initialized', 'planned', 'applied', 'destroyed', 'init_failed', 'planned_failed', 'apply_failed', 'destroy_failed'].includes(currentStatus);

        if (wasActive && isCompleted) {
          shouldInvalidateProject = true;

          // Show completion notifications for long-running operations
          // Use transition key to prevent duplicate notifications
          const transitionKey = `${module.id}:${previousStatus}:${currentStatus}`;
          
          if (!notifiedTransitionsRef.current.has(transitionKey)) {
            notifiedTransitionsRef.current.add(transitionKey);
            
            const moduleName = module.library_module?.name || 'Module';

            if (previousStatus === 'applying') {
              if (currentStatus === 'applied') {
                deploymentNotify.success(moduleName, 'apply');
              } else if (currentStatus === 'apply_failed') {
                deploymentNotify.failed(moduleName, 'apply');
              }
            } else if (previousStatus === 'destroying') {
              if (currentStatus === 'destroyed') {
                deploymentNotify.success(moduleName, 'destroy');
              } else if (currentStatus === 'destroy_failed') {
                deploymentNotify.failed(moduleName, 'destroy');
              }
            } else if (previousStatus === 'initializing') {
              if (currentStatus === 'initialized') {
                deploymentNotify.success(moduleName, 'initialize');
              } else if (currentStatus === 'init_failed') {
                deploymentNotify.failed(moduleName, 'initialize');
              }
            } else if (previousStatus === 'planning') {
              if (currentStatus === 'planned') {
                deploymentNotify.success(moduleName, 'plan');
              } else if (currentStatus === 'planned_failed') {
                deploymentNotify.failed(moduleName, 'plan');
              }
            }
            
            // Clear old transition keys after 5 seconds to allow future notifications
            setTimeout(() => {
              notifiedTransitionsRef.current.delete(transitionKey);
            }, 5000);
          }
        }
      }
    });

    // Update ref for next comparison
    previousStatusesRef.current = currentStatuses;

    // Invalidate project query to refresh counts
    if (shouldInvalidateProject && projectId) {
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
    }
  }, [query.data, projectId, queryClient]);

  return query;
}

export function useAddModuleToProject() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ projectId, data }: { projectId: number; data: AddModuleRequest }) =>
      api.addModuleToProject(projectId, data),
    onSuccess: (_, variables) => {
      // Invalidate modules for this project
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.project.byProject(variables.projectId) });
      // Invalidate all projects (counts will update)
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
    },
  });
}

export function useUpdateModule() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ moduleId, data }: { moduleId: number; data: Partial<ProjectModule> }) =>
      api.updateModule(moduleId, data),
    onSuccess: (data) => {
      // Invalidate all project modules (we don't know which project without the full module object)
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.project.all });
      // If the response includes project_id, we can invalidate more specifically
      if (data?.project_id) {
        queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(data.project_id) });
      }
    },
  });
}

export function useChangeModuleVersion() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ moduleId, targetVersion }: { moduleId: number; targetVersion: string }) =>
      api.changeModuleVersion(moduleId, targetVersion),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.project.all });
      notify.success('Module version changed', data?.message, { category: 'deployment' });
    },
  });
}

export function useDeleteModule() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (moduleId: number) => api.deleteModule(moduleId),
    onSuccess: () => {
      // Invalidate all project modules
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.project.all });
      // Invalidate all projects (counts will update)
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      notify.success('Module deleted', undefined, { category: 'deployment' });
    },
  });
}

export function useInitModule() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (moduleId: number) => api.initModule(moduleId),
    onSuccess: async (data) => {
      // Use refetchQueries so the fresh "initializing" status is in cache
      // before the useProjectModules polling refetchInterval evaluator runs.
      await queryClient.refetchQueries({ queryKey: queryKeys.modules.project.all });
      // If response includes project_id, invalidate that project specifically
      if (data?.project_id) {
        queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(data.project_id) });
      }

      // Persist a bell entry with a deep-link to the Tasks page for logs.
      notify.success('Initialize operation started', 'Open the Tasks page to monitor progress', {
        category: 'deployment',
        action_url: '/tasks',
      });
    },
  });
}

export function usePlanModule() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (moduleId: number) => api.planModule(moduleId),
    onSuccess: async (data) => {
      await queryClient.refetchQueries({ queryKey: queryKeys.modules.project.all });
      // If response includes project_id, invalidate that project specifically
      if (data?.project_id) {
        queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(data.project_id) });
      }
      // Don't show toast here - let the plan dialog handle success feedback
    },
  });
}

export function useApplyModule() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ moduleId, autoApprove, engineType: _engineType }: { moduleId: number; autoApprove?: boolean; engineType?: string }) =>
      api.applyModule(moduleId, autoApprove),
    onSuccess: async (data, variables) => {
      // Use refetchQueries so the fresh "applying" status is in cache
      // before the useProjectModules polling refetchInterval evaluator runs.
      await queryClient.refetchQueries({ queryKey: queryKeys.modules.project.all });
      // Invalidate all projects (counts will update - this is critical!)
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      // If response includes project_id, also invalidate that specific project
      if (data?.project_id) {
        queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(data.project_id) });
      }

      // SSH modules say "Run", others say "Apply"
      const action = variables.engineType === 'ssh' ? 'run' as const : 'apply' as const;
      deploymentNotify.started('Module', action);
    },
  });
}

export function useDestroyModule() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ moduleId, autoApprove }: { moduleId: number; autoApprove?: boolean }) =>
      api.destroyModule(moduleId, autoApprove),
    onSuccess: async (data) => {
      await queryClient.refetchQueries({ queryKey: queryKeys.modules.project.all });
      // Invalidate all projects (counts will update - this is critical!)
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      // If response includes project_id, also invalidate that specific project
      if (data?.project_id) {
        queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(data.project_id) });
      }
      deploymentNotify.started('Module', 'destroy');
    },
  });
}

export function useRerunModule() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (moduleId: number) => api.rerunModule(moduleId),
    onSuccess: async () => {
      await queryClient.refetchQueries({ queryKey: queryKeys.modules.project.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      // Invalidate stack data so error banner and status update immediately
      queryClient.invalidateQueries({ queryKey: ['stacks'] });
      deploymentNotify.started('Module', 'run');
    },
  });
}

export function useModuleStatus(moduleId: number) {
  return useQuery({
    queryKey: queryKeys.modules.project.status(moduleId),
    queryFn: () => api.getModuleStatus(moduleId),
    enabled: !!moduleId,
    refetchInterval: POLL_INTERVALS.FAST,
  });
}

export function useCancelDeployment() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (moduleId: number) => api.cancelDeployment(moduleId),
    onSuccess: (data) => {
      // Invalidate all project modules
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.project.all });
      // If response includes project_id, invalidate that project
      if (data?.project_id) {
        queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(data.project_id) });
      }
      notify.success('Deployment cancelled', undefined, { category: 'deployment' });
    },
  });
}

export function useRecoverState() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (moduleId: number) => api.recoverState(moduleId),
    onSuccess: (data) => {
      // Invalidate all project modules to refresh state indicators
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.project.all });
      // Invalidate tasks to show new recovery task
      queryClient.invalidateQueries({ queryKey: queryKeys.tasks.all });
      if (data?.project_id) {
        queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(data.project_id) });
      }
      notify.success('State recovery started', 'Reconnecting to infrastructure to rebuild state file...', {
        category: 'deployment',
      });
    },
  });
}

export function useSyncModuleLibrary() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (force?: boolean) => api.syncModuleLibrary(force || false),
    onSuccess: (data) => {
      // Invalidate all module library queries
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.all });
      const stats = data.stats || {};
      const total = stats.total_modules || 0;
      const created = stats.created || 0;
      const updated = stats.updated || 0;
      notify.success(`Module catalog synced`, `Total: ${total}, New: ${created}, Updated: ${updated}`, { category: 'system' });
    },
  });
}

// ============================================================================
// Variable Mapping Hooks
// ============================================================================

export function useVariableMappings(moduleId: number) {
  return useQuery({
    queryKey: queryKeys.modules.project.variableMappings(moduleId),
    queryFn: () => api.getVariableMappings(moduleId),
    enabled: !!moduleId,
  });
}

export function useCreateVariableMapping() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ moduleId, data }: { moduleId: number; data: VariableMappingRequest }) =>
      api.createVariableMapping(moduleId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.modules.project.variableMappings(variables.moduleId)
      });
    },
  });
}

export function useUpdateVariableMapping() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ moduleId, mappingId, data }: { moduleId: number; mappingId: number; data: VariableMappingRequest }) =>
      api.updateVariableMapping(moduleId, mappingId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.modules.project.variableMappings(variables.moduleId)
      });
    },
  });
}

export function useDeleteVariableMapping() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ moduleId, mappingId }: { moduleId: number; mappingId: number }) =>
      api.deleteVariableMapping(moduleId, mappingId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.modules.project.variableMappings(variables.moduleId)
      });
      notify.success('Variable mapping deleted', undefined, { category: 'deployment' });
    },
  });
}

export function useVariableMappingTemplates() {
  return useQuery({
    queryKey: queryKeys.modules.library.variableMappingTemplates(),
    queryFn: api.getVariableMappingTemplates,
  });
}

export function useCreateVariableMappingTemplate() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (data: VariableMappingTemplateRequest) => api.createVariableMappingTemplate(data),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.modules.library.variableMappingTemplates()
      });
    },
  });
}

export function useApplyVariableMappingTemplate() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ moduleId, templateId }: { moduleId: number; templateId: number }) =>
      api.applyVariableMappingTemplate(moduleId, templateId),
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.modules.project.variableMappings(variables.moduleId)
      });
      const created = data.created_count || 0;
      const skipped = data.skipped_count || 0;
      notify.success('Template applied', `${created} created, ${skipped} skipped`, { category: 'system' });
    },
  });
}
