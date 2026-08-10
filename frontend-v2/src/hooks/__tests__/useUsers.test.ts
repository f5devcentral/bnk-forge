/**
 * Tests for useUsers hooks (CT-024)
 *
 * Covers: user list query, create user, update user, delete user mutations.
 * Payload shape assertions verify data matches backend Pydantic schemas.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useUsers,
  useCreateUser,
  useUpdateUser,
  useDeleteUser,
} from '@/hooks/useUsers';
import React from 'react';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

// ============================================================================
// useUsers (query)
// ============================================================================

describe('useUsers', () => {
  it('fetches user list', async () => {
    server.use(
      http.get('*/api/auth/users', () => {
        return HttpResponse.json({
          users: [
            { id: 1, username: 'admin', email: 'admin@test.com', role: 'admin', is_active: true },
            { id: 2, username: 'operator1', email: 'op@test.com', role: 'operator', is_active: true },
          ],
          total: 2,
        });
      })
    );

    const { result } = renderHook(() => useUsers(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ total: 2 });
    expect(result.current.data!.users).toHaveLength(2);
  });
});

// ============================================================================
// CT-024: Payload Shape Assertions — useCreateUser
// Backend: UserCreateRequest { username, email, password, role }
// ============================================================================

describe('useCreateUser', () => {
  it('sends correct payload shape matching UserCreateRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/auth/users', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          user: { id: 3, username: capturedBody.username, email: capturedBody.email, role: capturedBody.role, is_active: true },
        });
      })
    );

    const { result } = renderHook(() => useCreateUser(), { wrapper: createWrapper() });

    result.current.mutate({
      username: 'newuser',
      email: 'new@example.com',
      password: 'SecurePass123!',
      role: 'operator',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      username: 'newuser',
      email: 'new@example.com',
      password: 'SecurePass123!',
      role: 'operator',
    });
    // Not wrapped
    expect(capturedBody).not.toHaveProperty('user');
    expect(capturedBody).not.toHaveProperty('data');
  });

  it('handles creation errors', async () => {
    server.use(
      http.post('*/api/auth/users', () => {
        return HttpResponse.json(
          { error: { code: 'DUPLICATE', message: 'Username already exists' } },
          { status: 409 }
        );
      })
    );

    const { result } = renderHook(() => useCreateUser(), { wrapper: createWrapper() });

    result.current.mutate({
      username: 'admin',
      email: 'dup@example.com',
      password: 'SecurePass123!',
      role: 'operator',
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});

// ============================================================================
// CT-024: Payload Shape Assertions — useUpdateUser
// Backend: UserUpdateRequest { email, role, is_active }
// ============================================================================

describe('useUpdateUser', () => {
  it('sends correct payload shape matching UserUpdateRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/auth/users/:userId', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          user: { id: 2, username: 'operator1', email: capturedBody.email || 'op@test.com', role: capturedBody.role || 'operator', is_active: capturedBody.is_active ?? true },
        });
      })
    );

    const { result } = renderHook(() => useUpdateUser(), { wrapper: createWrapper() });

    result.current.mutate({
      userId: 2,
      data: {
        email: 'updated@example.com',
        role: 'admin',
        is_active: false,
      },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      email: 'updated@example.com',
      role: 'admin',
      is_active: false,
    });
    // Not wrapped — fields are top-level
    expect(capturedBody).not.toHaveProperty('user');
    expect(capturedBody).not.toHaveProperty('userId'); // userId is in URL path, not body
    expect(capturedBody).not.toHaveProperty('user_id');
    expect(capturedBody).not.toHaveProperty('data');
  });

  it('sends partial update (only role)', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/auth/users/:userId', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          user: { id: 2, username: 'operator1', email: 'op@test.com', role: 'viewer', is_active: true },
        });
      })
    );

    const { result } = renderHook(() => useUpdateUser(), { wrapper: createWrapper() });

    result.current.mutate({
      userId: 2,
      data: { role: 'viewer' },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({ role: 'viewer' });
  });
});

// ============================================================================
// useDeleteUser (bodyless DELETE)
// ============================================================================

describe('useDeleteUser', () => {
  it('calls DELETE endpoint', async () => {
    let deletedUserId: string | null = null;
    server.use(
      http.delete('*/api/auth/users/:userId', ({ params }) => {
        deletedUserId = params.userId as string;
        return HttpResponse.json({ success: true, message: 'User deleted' });
      })
    );

    const { result } = renderHook(() => useDeleteUser(), { wrapper: createWrapper() });

    result.current.mutate(5);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(deletedUserId).toBe('5');
    expect(result.current.data).toMatchObject({ success: true });
  });
});
