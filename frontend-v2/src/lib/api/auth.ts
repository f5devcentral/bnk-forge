/**
 * Authentication API methods
 */
import { apiClient } from './client';
import type { User, UserCreateData, UserUpdateData, UserWithProjectCount } from '@/types';

export const authApi = {
  login: (username: string, password: string) =>
    apiClient.post<{ token: string; user: User; must_change_password: boolean }>(
      '/api/auth/login',
      { username, password }
    ).then((res) => res.data),

  getMe: () =>
    apiClient.get<{ user: User }>('/api/auth/me').then((res) => res.data.user),

  changePassword: (currentPassword: string, newPassword: string) =>
    apiClient.post<{ success: boolean; message: string }>(
      '/api/auth/change-password',
      { current_password: currentPassword, new_password: newPassword }
    ).then((res) => res.data),

  /** MU-006: Returns users with project counts */
  getUsers: () =>
    apiClient.get<{ users: UserWithProjectCount[]; total: number }>(
      '/api/auth/users'
    ).then((res) => res.data),

  /** MU-005: Create a new user */
  createUser: (data: UserCreateData) =>
    apiClient.post<{ success: boolean; user: User }>(
      '/api/auth/users',
      data
    ).then((res) => res.data),

  /** MU-005: Update user role, email, or active status */
  updateUser: (userId: number, data: UserUpdateData) =>
    apiClient.put<{ success: boolean; user: User }>(
      `/api/auth/users/${userId}`,
      data
    ).then((res) => res.data),

  deleteUser: (userId: number) =>
    apiClient.delete<{ success: boolean; message: string }>(
      `/api/auth/users/${userId}`
    ).then((res) => res.data),
};
