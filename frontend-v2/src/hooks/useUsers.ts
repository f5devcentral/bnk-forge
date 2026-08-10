/**
 * React Query hooks for user management (MU-007).
 *
 * Provides CRUD operations for user accounts (admin only).
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api';
import { QUERY_STALE_TIME } from '@/lib/constants';
import { notify } from '@/lib/notify';
import { queryKeys } from '@/lib/queryKeys';
import type { UserCreateData, UserUpdateData } from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

/** Fetch all users with project counts */
export function useUsers() {
  return useQuery({
    queryKey: queryKeys.auth.users(),
    queryFn: () => api.getUsers(),
    staleTime: QUERY_STALE_TIME.DEFAULT,
  });
}

/** Create a new user */
export function useCreateUser() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (data: UserCreateData) => api.createUser(data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.auth.users() });
      notify.success('User created', `${data.user.username} has been added`, { category: 'system' });
    },
  });
}

/** Update a user's role, email, or active status */
export function useUpdateUser() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ userId, data }: { userId: number; data: UserUpdateData }) =>
      api.updateUser(userId, data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.auth.users() });
      notify.success('User updated', `${result.user.username} has been updated`, { category: 'system' });
    },
  });
}

/** Delete a user */
export function useDeleteUser() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (userId: number) => api.deleteUser(userId),
    onSuccess: (_data, userId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.auth.users() });
      notify.success('User deleted', `User #${userId} has been removed`, { category: 'system' });
    },
  });
}
