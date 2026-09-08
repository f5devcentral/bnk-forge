/**
 * React Query hooks for BNK release source management (ADR-494).
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { releaseSourcesApi } from '@/lib/api/release-sources';
import type { ReleaseSourceCreate, ReleaseSourceUpdate } from '@/lib/api/release-sources';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export { type ReleaseSourceTagList, type PullTagsSummary } from '@/lib/api/release-sources';

export function useReleaseSources() {
  return useQuery({
    queryKey: queryKeys.releaseSources.list(),
    queryFn: () => releaseSourcesApi.list(),
  });
}

export function useCreateReleaseSource() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (payload: ReleaseSourceCreate) => releaseSourcesApi.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.releaseSources.all });
    },
  });
}

export function useUpdateReleaseSource() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ id, payload }: { id: number; payload: ReleaseSourceUpdate }) =>
      releaseSourcesApi.update(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.releaseSources.all });
    },
  });
}

export function useDeleteReleaseSource() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (id: number) => releaseSourcesApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.releaseSources.all });
    },
  });
}

export function useSyncReleaseSource() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ id, manifestYaml }: { id: number; manifestYaml: string }) =>
      releaseSourcesApi.sync(id, manifestYaml),
    onSuccess: () => {
      // Sync updates both the source metadata and the deployable releases catalog.
      queryClient.invalidateQueries({ queryKey: queryKeys.releaseSources.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.bareMetal.releases.all });
    },
  });
}

export function useReleaseSourceTags(id: number | null) {
  return useQuery({
    queryKey: queryKeys.releaseSources.tags(id ?? 0),
    queryFn: () => releaseSourcesApi.listTags(id!),
    enabled: id !== null,
    // Tags list is best-effort; stale for 60 s to avoid hammering the registry.
    staleTime: 60_000,
  });
}

export function usePullReleaseSourceTags() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ id, tags }: { id: number; tags: string[] }) =>
      releaseSourcesApi.pullTags(id, tags),
    onSuccess: (_data, variables) => {
      // Invalidate the source list (release_count changes) and the catalog.
      queryClient.invalidateQueries({ queryKey: queryKeys.releaseSources.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.releaseSources.tags(variables.id) });
      queryClient.invalidateQueries({ queryKey: queryKeys.bareMetal.releases.all });
    },
  });
}
