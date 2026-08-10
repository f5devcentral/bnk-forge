/**
 * React Query hooks for the Container Registries access method.
 *
 * Mirrors the SSH-credential data flow: a list query plus create / update /
 * delete / test mutations that invalidate the list. Container registries are
 * global (no project FK), name-unique, and never serialize their secrets.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';
import type {
  ContainerRegistry,
  ContainerRegistryCreate,
  ContainerRegistryUpdate,
  ContainerRegistryTestResult,
} from '@/lib/api/container-registries';

export type {
  ContainerRegistry,
  ContainerRegistryCreate,
  ContainerRegistryUpdate,
  ContainerRegistryTestResult,
};

export function useContainerRegistries() {
  return useQuery({
    queryKey: queryKeys.containerRegistries.list(),
    queryFn: () => api.listContainerRegistries(),
  });
}

export function useCreateContainerRegistry() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (data: ContainerRegistryCreate) => api.createContainerRegistry(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.containerRegistries.all });
    },
  });
}

export function useUpdateContainerRegistry() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ id, data }: { id: number; data: ContainerRegistryUpdate }) =>
      api.updateContainerRegistry(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.containerRegistries.all });
    },
  });
}

export function useDeleteContainerRegistry() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (id: number) => api.deleteContainerRegistry(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.containerRegistries.all });
    },
  });
}

export function useTestContainerRegistry() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (id: number) => api.testContainerRegistry(id),
    onSuccess: () => {
      // Refresh the list so each row picks up the persisted last_test_status.
      queryClient.invalidateQueries({ queryKey: queryKeys.containerRegistries.all });
    },
  });
}
