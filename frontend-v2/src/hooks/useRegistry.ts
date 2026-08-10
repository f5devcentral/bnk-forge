import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { QUERY_STALE_TIME } from '@/lib/constants';
import type { RegistryModuleImport } from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

/**
 * Hook to search OpenTofu/Terraform Registry modules
 */
export function useRegistrySearch(
  query: string = '',
  provider?: string,
  verified?: boolean,
  limit: number = 50,
  offset: number = 0,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.registry.search(query, provider, verified, limit, offset),
    queryFn: () =>
      api.searchRegistry({
        q: query,
        provider,
        verified,
        limit,
        offset,
      }),
    enabled: options?.enabled !== false,
    staleTime: QUERY_STALE_TIME.EXTENDED,
    gcTime: 1800000, // Keep in cache for 30 minutes
    refetchOnWindowFocus: false,
    refetchOnMount: false, // Use cached data on mount if available
  });
}

/**
 * Hook to get details of a specific registry module
 */
export function useRegistryModule(
  namespace: string | null,
  name: string | null,
  provider: string | null,
  useTerraformRegistry: boolean = false,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.registry.module(namespace, name, provider, useTerraformRegistry),
    queryFn: () => {
      if (!namespace || !name || !provider) {
        throw new Error('Missing required parameters');
      }
      return api.getRegistryModule(namespace, name, provider, useTerraformRegistry);
    },
    enabled: !!namespace && !!name && !!provider && (options?.enabled !== false),
    staleTime: QUERY_STALE_TIME.EXTENDED,
  });
}

/**
 * Hook to get available versions for a registry module
 */
export function useRegistryModuleVersions(
  namespace: string | null,
  name: string | null,
  provider: string | null,
  useTerraformRegistry: boolean = false,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.registry.moduleVersions(namespace, name, provider, useTerraformRegistry),
    queryFn: () => {
      if (!namespace || !name || !provider) {
        throw new Error('Missing required parameters');
      }
      return api.getRegistryModuleVersions(namespace, name, provider, useTerraformRegistry);
    },
    enabled: !!namespace && !!name && !!provider && (options?.enabled !== false),
    staleTime: QUERY_STALE_TIME.EXTENDED,
  });
}

/**
 * Mutation to import a module from the registry into the library
 */
export function useImportRegistryModule() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (data: RegistryModuleImport) => api.importRegistryModule(data),
    onSuccess: () => {
      // Invalidate module library queries to show the new imported module
      queryClient.invalidateQueries({
        queryKey: queryKeys.modules.library.all,
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.moduleSources.all,
      });
    },
  });
}

/**
 * Hook to check for module updates
 */
export function useCheckModuleUpdates(
  moduleId: number | null,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.registry.moduleUpdates(moduleId),
    queryFn: () => {
      if (!moduleId) throw new Error('Module ID required');
      return api.checkModuleUpdates(moduleId);
    },
    enabled: !!moduleId && (options?.enabled !== false),
    staleTime: QUERY_STALE_TIME.SHORT, // Check more frequently
  });
}

/**
 * Mutation to import a module from a public git URL
 */
export function useImportRegistryGit() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (data: { url: string; ref: string; subpath?: string; version_label?: string; display_name?: string }) =>
      api.importRegistryGit(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.moduleSources.all });
    },
  });
}

/**
 * Mutation to import a Terraform Cloud private module
 */
export function useImportRegistryTFC() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (data: { hostname: string; namespace: string; name: string; provider: string; version: string; credential_template_id: number }) =>
      api.importRegistryTFC(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.library.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.moduleSources.all });
    },
  });
}

/**
 * Mutation to upgrade a registry module to a newer version
 */
export function useUpgradeRegistryModule() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ moduleId, targetVersion }: { moduleId: number; targetVersion?: string }) =>
      api.upgradeRegistryModule(moduleId, targetVersion),
    onSuccess: (_, variables) => {
      // Invalidate module library queries to reflect the upgrade
      queryClient.invalidateQueries({
        queryKey: queryKeys.modules.library.all,
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.registry.moduleUpdates(variables.moduleId),
      });
    },
  });
}
