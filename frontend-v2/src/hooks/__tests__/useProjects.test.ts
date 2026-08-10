/**
 * Tests for useProjects hooks
 *
 * Covers: fetching projects list, creating/updating/deleting projects,
 * transferring ownership, variable defaults, project variables.
 * 
 * CRITICAL: Every mutation that sends a request body captures request.json()
 * via MSW and asserts the payload shape matches the backend Pydantic schema.
 * See: backend/schemas/projects.py
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useProjects,
  useCreateProject,
  useUpdateProject,
  useDeleteProject,
  useTransferOwnership,
  useUpdateVariableDefaults,
  useResetVariableDefaults,
  useUpdateProjectVariables,
} from '@/hooks/useProjects';
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
// useProjects (query)
// ============================================================================

describe('useProjects', () => {
  it('fetches projects and returns data', async () => {
    const { result } = renderHook(() => useProjects(), { wrapper: createWrapper() });

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toHaveLength(3);
    expect(result.current.data![0].name).toBe('test-project');
    expect(result.current.data![1].name).toBe('production-infra');
    expect(result.current.data![2].name).toBe('ibm-roks-prod');
  });

  it('returns loading state initially', () => {
    const { result } = renderHook(() => useProjects(), { wrapper: createWrapper() });

    expect(result.current.isLoading).toBe(true);
    expect(result.current.data).toBeUndefined();
  });

  it('handles API errors gracefully', async () => {
    server.use(
      http.get('*/api/projects', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Database connection failed' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useProjects(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });

  it('returns correct project shape', async () => {
    const { result } = renderHook(() => useProjects(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    const project = result.current.data![0];
    expect(project).toMatchObject({
      id: 1,
      name: 'test-project',
      project_type: 'cloud-aws',
      environment: 'dev',
      module_count: 2,
    });
  });
});

// ============================================================================
// useCreateProject — payload shape: ProjectCreate
// Backend schema: { name: str, description?: str, project_type?: str,
//   cloud_provider?: str, environment?: str, region?: str, ... }
// ============================================================================

describe('useCreateProject', () => {
  it('sends correct payload shape (ProjectCreate)', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          project_id: 3,
          name: capturedBody.name,
          message: 'Project created',
        });
      })
    );

    const { result } = renderHook(() => useCreateProject(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      name: 'new-project',
      description: 'A new project',
      project_type: 'cloud-ibm',
      environment: 'dev',
      region: 'us-south',
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // ProjectCreate schema: fields are at top level (no wrapper)
    expect(capturedBody).toMatchObject({
      name: 'new-project',
      description: 'A new project',
      project_type: 'cloud-ibm',
      environment: 'dev',
      region: 'us-south',
    });
  });

  it('handles creation errors', async () => {
    server.use(
      http.post('*/api/projects', () => {
        return HttpResponse.json(
          { error: { code: 'VALIDATION_ERROR', message: 'Name already exists' } },
          { status: 409 }
        );
      })
    );

    const { result } = renderHook(() => useCreateProject(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ name: 'duplicate-project' });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });

  it('sends ssh_credential_id when creating K8s project (K8S-014)', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          project_id: 10,
          name: capturedBody.name,
          message: 'Project created',
        });
      })
    );

    const { result } = renderHook(() => useCreateProject(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      name: 'k8s-project',
      project_type: 'kubernetes',
      environment: 'dev',
      ssh_credential_id: 5,
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody).toMatchObject({
      name: 'k8s-project',
      project_type: 'kubernetes',
      ssh_credential_id: 5,
    });
  });

  it('omits ssh_credential_id when not provided', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          project_id: 11,
          name: capturedBody.name,
          message: 'Project created',
        });
      })
    );

    const { result } = renderHook(() => useCreateProject(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      name: 'aws-project',
      project_type: 'cloud-aws',
      environment: 'prod',
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // ssh_credential_id should not be in the payload
    expect(capturedBody).not.toHaveProperty('ssh_credential_id');
  });
});

// ============================================================================
// useUpdateProject — payload shape: ProjectUpdate
// Backend schema: { name?: str, description?: str, project_type?: str, ... }
// ============================================================================

describe('useUpdateProject', () => {
  it('sends correct payload shape (ProjectUpdate)', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/projects/:id', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'Project updated' });
      })
    );

    const { result } = renderHook(() => useUpdateProject(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      id: 1,
      data: { name: 'updated-name', description: 'Updated description' },
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // ProjectUpdate: fields at top level (no wrapper)
    expect(capturedBody).toMatchObject({
      name: 'updated-name',
      description: 'Updated description',
    });
    // Should NOT be wrapped in a 'data' key
    expect(capturedBody).not.toHaveProperty('data');
    expect(capturedBody).not.toHaveProperty('id');
  });

  it('sends ssh_credential_id in update payload (K8S-014)', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/projects/:id', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'Project updated' });
      })
    );

    const { result } = renderHook(() => useUpdateProject(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      id: 1,
      data: { ssh_credential_id: 7 },
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody).toMatchObject({
      ssh_credential_id: 7,
    });
    expect(capturedBody).not.toHaveProperty('id');
  });
});

// ============================================================================
// useDeleteProject — no body (DELETE)
// ============================================================================

describe('useDeleteProject', () => {
  it('sends DELETE request to correct URL', async () => {
    let capturedUrl = '';
    server.use(
      http.delete('*/api/projects/:id', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({ success: true, message: 'Project deleted' });
      })
    );

    const { result } = renderHook(() => useDeleteProject(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(42);

    await waitFor(() => {
      expect(capturedUrl).toContain('/api/projects/42');
    });
  });
});

// ============================================================================
// useTransferOwnership — payload shape: TransferOwnershipRequest
// Backend schema: { new_owner_id: int }
// ============================================================================

describe('useTransferOwnership', () => {
  it('sends correct payload shape ({ new_owner_id })', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects/:projectId/transfer', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          project_id: 1,
          project_name: 'test-project',
          previous_owner: 'admin',
          new_owner: 'user2',
          message: 'Ownership transferred',
        });
      })
    );

    const { result } = renderHook(() => useTransferOwnership(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ projectId: 1, newOwnerId: 5 });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // TransferOwnershipRequest: { new_owner_id: int }
    expect(capturedBody).toMatchObject({
      new_owner_id: 5,
    });
    // Should NOT have camelCase 'newOwnerId' or 'projectId' in body
    expect(capturedBody).not.toHaveProperty('newOwnerId');
    expect(capturedBody).not.toHaveProperty('projectId');
  });
});

// ============================================================================
// useUpdateVariableDefaults — payload shape: VariableDefaultsUpdate
// Backend schema: { defaults: dict[str, Any] }
// BUG FOUND: was sending dict directly, now wrapped in { defaults }
// ============================================================================

describe('useUpdateVariableDefaults', () => {
  it('sends correct payload shape ({ defaults })', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/projects/:projectId/variable-defaults', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          message: 'Variable defaults updated',
          defaults_count: 2,
        });
      })
    );

    const { result } = renderHook(() => useUpdateVariableDefaults(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      projectId: 1,
      defaults: { region: 'us-west-2', instance_type: 't3.medium' },
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // VariableDefaultsUpdate: { defaults: dict }
    expect(capturedBody).toMatchObject({
      defaults: {
        region: 'us-west-2',
        instance_type: 't3.medium',
      },
    });
    // Ensure variables are nested under 'defaults', not at top level
    expect(capturedBody).not.toHaveProperty('region');
    expect(capturedBody).not.toHaveProperty('instance_type');
  });
});

// ============================================================================
// useResetVariableDefaults — no body (DELETE)
// ============================================================================

describe('useResetVariableDefaults', () => {
  it('sends DELETE to correct URL', async () => {
    let capturedUrl = '';
    server.use(
      http.delete('*/api/projects/:projectId/variable-defaults', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({ success: true, message: 'Variable defaults reset' });
      })
    );

    const { result } = renderHook(() => useResetVariableDefaults(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(7);

    await waitFor(() => {
      expect(capturedUrl).toContain('/api/projects/7/variable-defaults');
    });
  });
});

// ============================================================================
// useUpdateProjectVariables — payload shape: ProjectVariablesUpdate
// Backend schema: { variables: dict[str, Any] }
// BUG FOUND: was sending dict directly, now wrapped in { variables }
// ============================================================================

describe('useUpdateProjectVariables', () => {
  it('sends correct payload shape ({ variables })', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/projects/:projectId/variables', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          message: 'Project variables saved',
          variables_count: 2,
          variables: { env: 'production', debug: 'false' },
        });
      })
    );

    const { result } = renderHook(() => useUpdateProjectVariables(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      projectId: 1,
      variables: { env: 'production', debug: 'false' },
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // ProjectVariablesUpdate: { variables: dict }
    expect(capturedBody).toMatchObject({
      variables: {
        env: 'production',
        debug: 'false',
      },
    });
    // Ensure variables are nested, not at top level
    expect(capturedBody).not.toHaveProperty('env');
    expect(capturedBody).not.toHaveProperty('debug');
  });
});
