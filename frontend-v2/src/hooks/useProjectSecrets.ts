/**
 * React Query hooks for Project Secrets management
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { notify } from '@/lib/notify';
import type { ValueSecretCreate, ValueSecretUpdate } from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// Query keys (re-export from centralized factory for backward compatibility)
export const secretKeys = {
  all: queryKeys.secrets.all,
  list: (projectId: number) => queryKeys.secrets.list(projectId),
  required: (projectId: number, stackSlug?: string) => queryKeys.secrets.required(projectId, stackSlug),
};

// Helper to invalidate all secret queries for a project
const invalidateSecretQueries = (queryClient: ReturnType<typeof useQueryClient>, projectId: number) => {
  queryClient.invalidateQueries({ queryKey: queryKeys.secrets.list(projectId) });
  queryClient.invalidateQueries({ queryKey: queryKeys.secrets.required(projectId) });
};

/**
 * List all secrets for a project
 */
export function useProjectSecrets(projectId: number) {
  return useQuery({
    queryKey: secretKeys.list(projectId),
    queryFn: () => api.listProjectSecrets(projectId),
    enabled: !!projectId,
    staleTime: 30 * 1000, // 30 seconds
  });
}

/**
 * Get required secrets for a project or stack
 * PERF-016: Added options parameter for lazy loading support
 */
export function useRequiredSecrets(
  projectId: number, 
  options?: { stackSlug?: string; enabled?: boolean }
) {
  const { stackSlug, enabled = true } = options || {};
  
  return useQuery({
    queryKey: secretKeys.required(projectId, stackSlug),
    queryFn: () => api.getRequiredSecrets(projectId, stackSlug),
    enabled: !!projectId && enabled,
    staleTime: 60 * 1000, // 1 minute
  });
}

/**
 * Create a file secret (upload)
 */
export function useCreateFileSecret(projectId: number) {
  const queryClient = useQueryClient();
  
  return useAppMutation({
    mutationFn: async ({
      file,
      name,
      description,
      targetModulePath,
      targetVariableName,
    }: {
      file: File;
      name: string;
      description?: string;
      targetModulePath?: string;
      targetVariableName?: string;
    }) => {
      return api.createFileSecret(projectId, file, name, description, targetModulePath, targetVariableName);
    },
    onSuccess: (data) => {
      invalidateSecretQueries(queryClient, projectId);
      notify.success(`File secret "${data.secret.name}" uploaded successfully`, undefined, { category: 'credentials' });
    },
    onError: (error: Error) => {
      notify.error('Failed to upload file secret', error.message, { category: 'credentials' });
    },
  });
}

/**
 * Create a value secret
 */
export function useCreateValueSecret(projectId: number) {
  const queryClient = useQueryClient();
  
  return useAppMutation({
    mutationFn: (data: ValueSecretCreate) => api.createValueSecret(projectId, data),
    onSuccess: (data) => {
      invalidateSecretQueries(queryClient, projectId);
      notify.success(`Secret "${data.secret.name}" created successfully`, undefined, { category: 'credentials' });
    },
    onError: (error: Error) => {
      notify.error('Failed to create secret', error.message, { category: 'credentials' });
    },
  });
}

/**
 * Import FAR/JWT special secrets from uploaded files
 */
export function useImportSpecialSecret(projectId: number) {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ secretName, file }: { secretName: 'cne_pull_secret' | 'jwt_token'; file: File }) =>
      api.importSpecialSecret(projectId, secretName, file),
    onSuccess: (data) => {
      invalidateSecretQueries(queryClient, projectId);
      notify.success(`Secret "${data.secret.name}" ${data.action} from file`, undefined, { category: 'credentials' });
    },
    onError: (error: Error) => {
      notify.error('Failed to import secret from file', error.message, { category: 'credentials' });
    },
  });
}

/**
 * Update a value secret
 */
export function useUpdateValueSecret(projectId: number) {
  const queryClient = useQueryClient();
  
  return useAppMutation({
    mutationFn: ({ secretId, data }: { secretId: number; data: ValueSecretUpdate }) => 
      api.updateValueSecret(projectId, secretId, data),
    onSuccess: () => {
      invalidateSecretQueries(queryClient, projectId);
      notify.success('Secret updated successfully', undefined, { category: 'credentials' });
    },
    onError: (error: Error) => {
      notify.error('Failed to update secret', error.message, { category: 'credentials' });
    },
  });
}

/**
 * Delete a secret
 */
export function useDeleteProjectSecret(projectId: number) {
  const queryClient = useQueryClient();
  
  return useAppMutation({
    mutationFn: (secretId: number) => api.deleteProjectSecret(projectId, secretId),
    onSuccess: () => {
      invalidateSecretQueries(queryClient, projectId);
      notify.success('Secret deleted successfully', undefined, { category: 'credentials' });
    },
    onError: (error: Error) => {
      notify.error('Failed to delete secret', error.message, { category: 'credentials' });
    },
  });
}

/**
 * Update a stack variable secret (wizard-provided value)
 */
export function useUpdateStackVariableSecret(projectId: number) {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) =>
      api.updateStackVariableSecret(projectId, name, value),
    onSuccess: () => {
      invalidateSecretQueries(queryClient, projectId);
      notify.success('Secret updated successfully', undefined, { category: 'credentials' });
    },
    onError: (error: Error) => {
      notify.error('Failed to update secret', error.message, { category: 'credentials' });
    },
  });
}

/**
 * Delete (clear) a stack variable secret
 */
export function useDeleteStackVariableSecret(projectId: number) {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (name: string) => api.deleteStackVariableSecret(projectId, name),
    onSuccess: () => {
      invalidateSecretQueries(queryClient, projectId);
      notify.success('Secret removed', undefined, { category: 'credentials' });
    },
    onError: (error: Error) => {
      notify.error('Failed to remove secret', error.message, { category: 'credentials' });
    },
  });
}
