import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { QUERY_STALE_TIME } from '@/lib/constants';
import type { ProjectCreateData, ProjectUpdateData, JsonValue } from '@/types';
import { notify } from '@/lib/notify';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export function useProjects() {
  return useQuery({
    queryKey: queryKeys.projects.list(),
    queryFn: api.getProjects,
    staleTime: 30000, // 30 seconds - projects list doesn't change frequently
  });
}

export function useProject(id: number) {
  return useQuery({
    queryKey: queryKeys.projects.detail(id),
    queryFn: () => api.getProject(id),
    enabled: !!id,
    // Keep previous data while refetching to prevent UI flicker
    placeholderData: (previousData) => previousData,
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (data: ProjectCreateData) => api.createProject(data),
    onSuccess: () => {
      // Invalidate all projects queries (list and all details)
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
    },
  });
}

export function useUpdateProject() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ id, data }: { id: number; data: ProjectUpdateData }) =>
      api.updateProject(id, data),
    onSuccess: (_, variables) => {
      // Invalidate all projects (list will show updated data)
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      // Also invalidate specific project detail
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(variables.id) });
    },
  });
}

export function useDeleteProject() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (id: number) => api.deleteProject(id),
    onSuccess: (_, id) => {
      // Invalidate all projects queries
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      // Invalidate related data that references this project
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.project.byProject(id) });
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', 'project', id] });
      queryClient.invalidateQueries({ queryKey: ['stacks', 'instances', id] });
      queryClient.invalidateQueries({ queryKey: ['project-secrets', 'list', id] });
      notify.success('Project deleted successfully', undefined, { category: 'general' });
    },
  });
}

/** MU-009: Transfer project ownership to another user */
export function useTransferOwnership() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ projectId, newOwnerId }: { projectId: number; newOwnerId: number }) =>
      api.transferOwnership(projectId, newOwnerId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(result.project_id) });
      // Also refresh user list (project counts change)
      queryClient.invalidateQueries({ queryKey: queryKeys.auth.users() });
      notify.success('Ownership transferred', result.message, { category: 'general' });
    },
  });
}

// Variable Defaults hooks
export function useVariableDefaults(projectId: number) {
  return useQuery({
    queryKey: queryKeys.projects.variableDefaults(projectId),
    queryFn: () => api.getVariableDefaults(projectId),
    enabled: !!projectId,
  });
}

export function useUpdateVariableDefaults() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ projectId, defaults }: { projectId: number; defaults: Record<string, JsonValue> }) =>
      api.updateVariableDefaults(projectId, defaults),
    onSuccess: (_, variables) => {
      // Invalidate variable defaults for this project
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.variableDefaults(variables.projectId) });
      // Invalidate project detail (in case it shows default count)
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(variables.projectId) });
    },
  });
}

export function useResetVariableDefaults() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (projectId: number) => api.resetVariableDefaults(projectId),
    onSuccess: (_, projectId) => {
      // Invalidate variable defaults for this project
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.variableDefaults(projectId) });
      // Invalidate project detail (in case it shows default count)
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });
    },
  });
}

// Project Variables hooks (replaces Variable Defaults)
export function useProjectVariables(projectId: number) {
  return useQuery({
    queryKey: queryKeys.projects.variables(projectId),
    queryFn: () => api.getProjectVariables(projectId),
    enabled: !!projectId,
    staleTime: QUERY_STALE_TIME.DEFAULT, // 30 seconds - variables don't change frequently
    placeholderData: (prev) => prev, // Keep previous data while refetching
  });
}

export function useUpdateProjectVariables() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ projectId, variables }: { projectId: number; variables: Record<string, JsonValue> }) =>
      api.updateProjectVariables(projectId, variables),
    onSuccess: (_, params) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.variables(params.projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(params.projectId) });
    },
  });
}
