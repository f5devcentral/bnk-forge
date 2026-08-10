/**
 * Tests for useProjectSecrets hooks
 *
 * Covers: useProjectSecrets fetches project secrets list,
 * useRequiredSecrets fetches required/satisfied/missing secrets,
 * useCreateValueSecret creates a value secret via mutation,
 * useDeleteProjectSecret deletes a secret via mutation,
 * and disabled-query behavior when projectId is falsy.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useProjectSecrets,
  useRequiredSecrets,
  useCreateValueSecret,
  useImportSpecialSecret,
  useUpdateValueSecret,
  useDeleteProjectSecret,
  useUpdateStackVariableSecret,
  useDeleteStackVariableSecret,
} from '@/hooks/useProjectSecrets';
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
// Mock Data
// ============================================================================

const mockSecrets = {
  success: true,
  secrets: [
    {
      id: 1,
      name: 'AWS_ACCESS_KEY_ID',
      project_id: 1,
      is_file: false,
      created_at: '2026-01-15T00:00:00Z',
      updated_at: '2026-01-15T00:00:00Z',
    },
    {
      id: 2,
      name: 'kubeconfig.yaml',
      project_id: 1,
      is_file: true,
      created_at: '2026-01-20T00:00:00Z',
      updated_at: '2026-01-20T00:00:00Z',
    },
  ],
  count: 2,
};

const mockRequiredSecrets = {
  required_secrets: ['AWS_ACCESS_KEY', 'AWS_SECRET_KEY'],
  satisfied: ['AWS_ACCESS_KEY'],
  missing: ['AWS_SECRET_KEY'],
};

// ============================================================================
// Handlers
// ============================================================================

const secretHandlers = [
  http.get('*/api/projects/:projectId/secrets', ({ request }) => {
    const url = new URL(request.url);
    // Don't match sub-paths like /secrets/required or /secrets/value
    if (!url.pathname.endsWith('/secrets')) return;
    return HttpResponse.json(mockSecrets);
  }),

  http.get('*/api/projects/:projectId/secrets/required', () => {
    return HttpResponse.json(mockRequiredSecrets);
  }),

  http.post('*/api/projects/:projectId/secrets/value', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      success: true,
      secret: {
        id: 10,
        name: body.name,
        is_file: false,
        created_at: new Date().toISOString(),
      },
    });
  }),

  http.delete('*/api/projects/:projectId/secrets/:secretId', () => {
    return HttpResponse.json({ success: true });
  }),

  http.post('*/api/projects/:projectId/secrets/import', async ({ request }) => {
    const body = await request.formData();
    const secretName = String(body.get('secret_name') || 'unknown');
    return HttpResponse.json({
      success: true,
      action: 'created',
      secret: { id: 11, name: secretName, secret_type: 'value' },
    });
  }),
];

// ============================================================================
// Tests
// ============================================================================

describe('useProjectSecrets', () => {
  beforeEach(() => {
    server.use(...secretHandlers);
  });

  it('fetches project secrets list', async () => {
    const { result } = renderHook(() => useProjectSecrets(1), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.secrets).toHaveLength(2);
    expect(result.current.data?.secrets[0].name).toBe('AWS_ACCESS_KEY_ID');
    expect(result.current.data?.secrets[1].is_file).toBe(true);
  });

  it('does not fetch when projectId is 0 (falsy)', () => {
    const { result } = renderHook(() => useProjectSecrets(0), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(false);
    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useRequiredSecrets', () => {
  beforeEach(() => {
    server.use(...secretHandlers);
  });

  it('fetches required secrets for a project', async () => {
    const { result } = renderHook(() => useRequiredSecrets(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      required_secrets: ['AWS_ACCESS_KEY', 'AWS_SECRET_KEY'],
      satisfied: ['AWS_ACCESS_KEY'],
      missing: ['AWS_SECRET_KEY'],
    });
  });

  it('respects enabled option', () => {
    const { result } = renderHook(
      () => useRequiredSecrets(1, { enabled: false }),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useCreateValueSecret', () => {
  beforeEach(() => {
    server.use(...secretHandlers);
  });

  it('creates a value secret and returns result', async () => {
    const { result } = renderHook(() => useCreateValueSecret(1), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      name: 'DB_PASSWORD',
      value: 'super-secret',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.secret.name).toBe('DB_PASSWORD');
    expect(result.current.data?.success).toBe(true);
  });

  it('handles mutation errors', async () => {
    server.use(
      http.post('*/api/projects/:projectId/secrets/value', () => {
        return HttpResponse.json(
          { error: { code: 'VALIDATION_ERROR', message: 'Secret name already exists' } },
          { status: 400 }
        );
      })
    );

    const { result } = renderHook(() => useCreateValueSecret(1), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      name: 'DUPLICATE_SECRET',
      value: 'value',
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});

// ============================================================================
// CT-021: Payload Shape Assertions — useCreateValueSecret
// Backend: ValueSecretCreate { name, value, description, target_module_path, target_variable_name }
// ============================================================================

describe('useCreateValueSecret payload shape', () => {
  it('sends correct payload matching ValueSecretCreate', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects/:projectId/secrets/value', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, secret: { id: 1, name: capturedBody.name, type: 'value' } });
      })
    );

    const { result } = renderHook(() => useCreateValueSecret(1), { wrapper: createWrapper() });

    result.current.mutate({
      name: 'API_KEY',
      value: 'sk-12345',
      description: 'External API key',
      target_module_path: 'infra/vpc',
      target_variable_name: 'api_key',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      name: 'API_KEY',
      value: 'sk-12345',
      description: 'External API key',
      target_module_path: 'infra/vpc',
      target_variable_name: 'api_key',
    });
    expect(capturedBody).not.toHaveProperty('secret');
    expect(capturedBody).not.toHaveProperty('project_id');
  });
});

// ============================================================================
// CT-021: Payload Shape Assertions — useUpdateValueSecret
// Backend: ValueSecretUpdate { value, description, target_module_path, target_variable_name }
// ============================================================================

describe('useUpdateValueSecret payload shape', () => {
  it('sends correct payload matching ValueSecretUpdate', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/projects/:projectId/secrets/:secretId/value', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, secret: { id: 1, name: 'API_KEY', type: 'value' } });
      })
    );

    const { result } = renderHook(() => useUpdateValueSecret(1), { wrapper: createWrapper() });

    result.current.mutate({
      secretId: 1,
      data: {
        value: 'sk-updated-67890',
        description: 'Updated API key',
        target_module_path: 'infra/nginx',
        target_variable_name: 'api_key_v2',
      },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      value: 'sk-updated-67890',
      description: 'Updated API key',
      target_module_path: 'infra/nginx',
      target_variable_name: 'api_key_v2',
    });
    // name is NOT in update schema
    expect(capturedBody).not.toHaveProperty('name');
    expect(capturedBody).not.toHaveProperty('secret_id');
  });
});

describe('useDeleteProjectSecret', () => {
  beforeEach(() => {
    server.use(...secretHandlers);
  });

  it('deletes a secret and returns success', async () => {
    const { result } = renderHook(() => useDeleteProjectSecret(1), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });

  it('handles deletion errors', async () => {
    server.use(
      http.delete('*/api/projects/:projectId/secrets/:secretId', () => {
        return HttpResponse.json(
          { error: { code: 'NOT_FOUND', message: 'Secret not found' } },
          { status: 404 }
        );
      })
    );

    const { result } = renderHook(() => useDeleteProjectSecret(1), {
      wrapper: createWrapper(),
    });

    result.current.mutate(999);

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});

describe('useImportSpecialSecret', () => {
  beforeEach(() => {
    server.use(...secretHandlers);
  });

  it('imports jwt_token from uploaded file', async () => {
    const { result } = renderHook(() => useImportSpecialSecret(1), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      secretName: 'jwt_token',
      file: new File(['header.payload.signature'], 'license.jwt', { type: 'text/plain' }),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.secret.name).toBe('jwt_token');
    expect(result.current.data?.action).toBe('created');
  });
});

// ============================================================================
// SEC-001: useUpdateStackVariableSecret
// Backend: PUT /api/projects/{id}/secrets/stack-variable
// Body: StackVariableSecretUpdate { name: string, value: string }
// ============================================================================

describe('useUpdateStackVariableSecret', () => {
  it('updates a stack variable secret and returns success', async () => {
    server.use(
      http.put('*/api/projects/:projectId/secrets/stack-variable', () => {
        return HttpResponse.json({ success: true, name: 'jwt_token' });
      })
    );

    const { result } = renderHook(() => useUpdateStackVariableSecret(1), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ name: 'jwt_token', value: 'new-token-value' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true, name: 'jwt_token' });
  });

  it('handles update errors (stack not found)', async () => {
    server.use(
      http.put('*/api/projects/:projectId/secrets/stack-variable', () => {
        return HttpResponse.json(
          { error: { code: 'NOT_FOUND', message: 'Active stack instance not found' } },
          { status: 404 }
        );
      })
    );

    const { result } = renderHook(() => useUpdateStackVariableSecret(1), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ name: 'jwt_token', value: 'value' });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});

// CT-021: Payload Shape Assertions — useUpdateStackVariableSecret
describe('useUpdateStackVariableSecret payload shape', () => {
  it('sends { name, value } matching StackVariableSecretUpdate schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/projects/:projectId/secrets/stack-variable', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, name: capturedBody.name });
      })
    );

    const { result } = renderHook(() => useUpdateStackVariableSecret(1), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ name: 'cne_pull_secret', value: 'base64-encoded-content' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      name: 'cne_pull_secret',
      value: 'base64-encoded-content',
    });
    // Should only have name and value, no extra fields
    expect(Object.keys(capturedBody!)).toEqual(['name', 'value']);
  });
});

// ============================================================================
// SEC-001: useDeleteStackVariableSecret
// Backend: DELETE /api/projects/{id}/secrets/stack-variable/{name}
// ============================================================================

describe('useDeleteStackVariableSecret', () => {
  it('deletes a stack variable secret and returns success', async () => {
    server.use(
      http.delete('*/api/projects/:projectId/secrets/stack-variable/:name', () => {
        return HttpResponse.json({ success: true });
      })
    );

    const { result } = renderHook(() => useDeleteStackVariableSecret(1), {
      wrapper: createWrapper(),
    });

    result.current.mutate('jwt_token');

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });

  it('handles deletion errors (variable not found)', async () => {
    server.use(
      http.delete('*/api/projects/:projectId/secrets/stack-variable/:name', () => {
        return HttpResponse.json(
          { error: { code: 'NOT_FOUND', message: 'Stack variable not found' } },
          { status: 404 }
        );
      })
    );

    const { result } = renderHook(() => useDeleteStackVariableSecret(1), {
      wrapper: createWrapper(),
    });

    result.current.mutate('nonexistent_var');

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});
