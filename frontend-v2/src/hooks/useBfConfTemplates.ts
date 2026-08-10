/**
 * React Query hooks for the bf.conf template catalog.
 *
 * List is viewer-scope (also used by the Project DPU Settings dropdown);
 * create / update / delete are operator-only admin mutations.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  bfConfTemplatesApi,
  type BfConfTemplateCreate,
  type BfConfTemplateUpdate,
} from '@/lib/api/bf-conf-templates';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export function useBfConfTemplates() {
  return useQuery({
    queryKey: queryKeys.dpuProvisioning.bfConfTemplates.all,
    queryFn: () => bfConfTemplatesApi.list(),
  });
}

export function useBfConfTemplate(id: number | null) {
  return useQuery({
    queryKey: [...queryKeys.dpuProvisioning.bfConfTemplates.all, id],
    queryFn: () => bfConfTemplatesApi.get(id as number),
    enabled: id != null,
  });
}

export function useCreateBfConfTemplate() {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (data: BfConfTemplateCreate) => bfConfTemplatesApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.bfConfTemplates.all });
    },
  });
}

export function useUpdateBfConfTemplate() {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: ({ id, data }: { id: number; data: BfConfTemplateUpdate }) =>
      bfConfTemplatesApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.bfConfTemplates.all });
    },
  });
}

export function useDeleteBfConfTemplate() {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (id: number) => bfConfTemplatesApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.bfConfTemplates.all });
    },
  });
}
