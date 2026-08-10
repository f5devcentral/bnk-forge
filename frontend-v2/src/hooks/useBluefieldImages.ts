/**
 * React Query hooks for Bluefield Software Images (unified BFB + DOCA catalog).
 *
 * Source-of-truth for BFB / DOCA choices offered on the DPU tab and
 * bare-metal blueprint variable editors.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  bluefieldImagesApi,
  type CreateBluefieldImageData,
  type UpdateBluefieldImageData,
} from '@/lib/api/bluefield-images';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export function useBluefieldImages() {
  return useQuery({
    queryKey: queryKeys.dpuProvisioning.bluefieldImages.all,
    queryFn: () => bluefieldImagesApi.list(),
  });
}

export function useDefaultBluefieldImage() {
  return useQuery({
    queryKey: queryKeys.dpuProvisioning.bluefieldImages.default,
    queryFn: () => bluefieldImagesApi.getDefault(),
  });
}

export function useCreateBluefieldImage() {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (data: CreateBluefieldImageData) => bluefieldImagesApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.bluefieldImages.all });
    },
  });
}

export function useUpdateBluefieldImage() {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: ({ id, data }: { id: number; data: UpdateBluefieldImageData }) =>
      bluefieldImagesApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.bluefieldImages.all });
    },
  });
}

export function useDeleteBluefieldImage() {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (id: number) => bluefieldImagesApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.bluefieldImages.all });
    },
  });
}
