import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { notify } from '@/lib/notify';
import type { ModuleSourceCreate, ModuleSourceUpdate } from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// Query keys (re-export from centralized factory for backward compatibility)
export const moduleSourceKeys = {
  all: queryKeys.moduleSources.all,
  lists: () => queryKeys.moduleSources.lists(),
  list: (filters?: { source_type?: string; is_active?: boolean }) =>
    queryKeys.moduleSources.list(filters),
  details: () => queryKeys.moduleSources.details(),
  detail: (id: number) => queryKeys.moduleSources.detail(id),
  modules: (id: number) => queryKeys.moduleSources.modules(id),
};

// Hooks
export function useModuleSources(params?: { source_type?: string; is_active?: boolean }) {
  return useQuery({
    queryKey: moduleSourceKeys.list(params),
    queryFn: () => api.getModuleSources(params),
  });
}

export function useModuleSource(sourceId: number) {
  return useQuery({
    queryKey: moduleSourceKeys.detail(sourceId),
    queryFn: () => api.getModuleSource(sourceId),
    enabled: !!sourceId,
  });
}

export function useSourceModules(sourceId: number) {
  return useQuery({
    queryKey: moduleSourceKeys.modules(sourceId),
    queryFn: () => api.getSourceModules(sourceId),
    enabled: !!sourceId,
  });
}

export function useCreateModuleSource() {
  const queryClient = useQueryClient();
  const syncModuleSource = useSyncModuleSource();

  return useAppMutation({
    mutationFn: (data: ModuleSourceCreate) => api.createModuleSource(data),
    onSuccess: async (createdSource) => {
      await syncModuleSource.mutateAsync(createdSource.id);

      queryClient.invalidateQueries({ queryKey: moduleSourceKeys.all });
      queryClient.invalidateQueries({ queryKey: moduleSourceKeys.lists() });
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.lists() });
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.providers() });
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.categories() });
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.sources() });
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.releases() });
    },
  });
}

export function useUpdateModuleSource() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ sourceId, data }: { sourceId: number; data: ModuleSourceUpdate }) =>
      api.updateModuleSource(sourceId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: moduleSourceKeys.detail(variables.sourceId) });
      queryClient.invalidateQueries({ queryKey: moduleSourceKeys.lists() });
    },
  });
}

export function useDeleteModuleSource() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (sourceId: number) => api.deleteModuleSource(sourceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: moduleSourceKeys.all });
      notify.success('Module source deleted', undefined, { category: 'system' });
    },
  });
}

export function useSyncModuleSource() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (sourceId: number) => api.syncModuleSource(sourceId),
    onSuccess: (data, sourceId) => {
      queryClient.invalidateQueries({ queryKey: moduleSourceKeys.all });
      queryClient.invalidateQueries({ queryKey: moduleSourceKeys.lists() });
      queryClient.invalidateQueries({ queryKey: queryKeys.moduleSources.detail(sourceId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.moduleSources.modules(sourceId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.all }); // Invalidate module library
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.lists() });
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.providers() });
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.categories() });

      const { results } = data;
      const message = `Sync complete: ${results.modules_created} created, ${results.modules_updated} updated`;

      if (results.errors.length > 0) {
        notify.warning(message, `${results.errors.length} errors occurred`, { category: 'system' });
      } else {
        notify.success(message, undefined, { category: 'system' });
      }
    },
  });
}
