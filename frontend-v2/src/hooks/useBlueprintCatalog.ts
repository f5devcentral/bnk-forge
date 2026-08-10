import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAppMutation } from '@/hooks/lib/useAppMutation';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { notify } from '@/lib/notify';
import { stackKeys } from '@/hooks/useStacks';
import type { BlueprintRelease, BlueprintSourceCreate, BlueprintSourceUpdate } from '@/types';

export function useBlueprintSources(params?: { source_type?: string; is_active?: boolean }) {
  return useQuery({
    queryKey: queryKeys.blueprintCatalog.sourceList(params),
    queryFn: () => api.getBlueprintSources(params),
  });
}

export function useBlueprintReleases(params?: {
  source_id?: number;
  blueprint_id?: string;
  release_state?: string;
  validation_state?: string;
  is_active?: boolean;
}) {
  return useQuery({
    queryKey: queryKeys.blueprintCatalog.releaseList(params),
    queryFn: () => api.getBlueprintReleases(params),
  });
}

export function useCreateBlueprintSource() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (data: BlueprintSourceCreate) => api.createBlueprintSource(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.sources() });
    },
  });
}

export function useSyncBlueprintSource() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (sourceId: number) => api.syncBlueprintSource(sourceId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.sources() });
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.releases() });
      queryClient.invalidateQueries({ queryKey: stackKeys.templates() });
      const { results } = data;
      const summary = `Blueprint sync complete: ${results.blueprints_found} manifests found, ${results.releases_created} discovered, ${results.releases_existing} unchanged${results.releases_conflicted ? `, ${results.releases_conflicted} conflicted` : ''}`;
      if (results.errors.length > 0) {
        if (results.releases_conflicted && results.releases_conflicted === results.errors.length) {
          notify.warning(summary, `${results.releases_conflicted} manifest${results.releases_conflicted === 1 ? '' : 's'} already exist in the catalog with different content. Delete or version-bump the existing release to replace them.`, { category: 'system' });
        } else {
          notify.warning(summary, `${results.errors.length} manifest errors occurred`, { category: 'system' });
        }
      } else if (results.blueprints_found === 0) {
        notify.info(summary, 'The source synced successfully, but no forge-blueprint.json manifests were found.', { category: 'system' });
      } else {
        notify.success(summary, undefined, { category: 'system' });
      }
    },
  });
}

export function useDeleteBlueprintSource() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (sourceId: number) => api.deleteBlueprintSource(sourceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.sources() });
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.releases() });
      queryClient.invalidateQueries({ queryKey: stackKeys.templates() });
      notify.success('Blueprint source deleted', undefined, { category: 'system' });
    },
  });
}

export function useUpdateBlueprintSource() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ sourceId, data }: { sourceId: number; data: BlueprintSourceUpdate }) =>
      api.updateBlueprintSource(sourceId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.sources() });
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.releases() });
    },
  });
}

export function useImportBlueprintRelease() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ releaseId, stateReason }: { releaseId: number; stateReason?: string }) =>
      api.importBlueprintRelease(releaseId, { state_reason: stateReason }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.releases() });
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.sources() });
      queryClient.invalidateQueries({ queryKey: stackKeys.templates() });
    },
  });
}

export function useDeleteBlueprintRelease() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (releaseId: number) => api.deleteBlueprintRelease(releaseId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.releases() });
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.sources() });
      queryClient.invalidateQueries({ queryKey: stackKeys.templates() });
      notify.success('Blueprint release deleted', undefined, { category: 'system' });
    },
  });
}

export function useUnimportBlueprintRelease() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (releaseId: number) => api.unimportBlueprintRelease(releaseId),
    onSuccess: (updated) => {
      // Patch every cached releases-list query in place so the card flips
      // to 'discovered' (Import button visible, Deploy disabled) without
      // waiting for a refetch round-trip. Then invalidate so the next
      // background fetch reconciles with the server.
      queryClient.setQueriesData<BlueprintRelease[]>(
        { queryKey: queryKeys.blueprintCatalog.releases() },
        (current) =>
          (current || []).map((release) => (release.id === updated.id ? updated : release)),
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.releases() });
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.sources() });
      queryClient.invalidateQueries({ queryKey: stackKeys.templates() });
      notify.success('Blueprint release removed from deployable catalog', undefined, { category: 'system' });
    },
  });
}

export function useSetBlueprintReleaseVisibility() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ releaseId, isVisible }: { releaseId: number; isVisible: boolean }) =>
      api.setBlueprintReleaseVisibility(releaseId, isVisible),
    onSuccess: (updated) => {
      queryClient.setQueriesData<BlueprintRelease[]>(
        { queryKey: queryKeys.blueprintCatalog.releases() },
        (current) =>
          (current || []).map((release) => (release.id === updated.id ? updated : release)),
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.releases() });
      queryClient.invalidateQueries({ queryKey: stackKeys.templates() });
      notify.success(updated.is_active ? 'Blueprint visible on Blueprints page' : 'Blueprint hidden from Blueprints page');
    },
  });
}
