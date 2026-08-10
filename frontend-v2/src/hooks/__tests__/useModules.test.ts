/**
 * Tests for useModules hooks
 *
 * Covers: useModuleLibrary fetches catalog from /api/module-library,
 * useModuleDetails fetches single module, useProjectModules fetches
 * project-scoped modules, and mutation hooks (add, delete, init, plan,
 * apply, destroy) call the correct endpoints and handle errors.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useModuleLibrary,
  useModuleDetails,
  useProjectModules,
  useAddModuleToProject,
  useUpdateModule,
  useChangeModuleVersion,
  useDeleteModule,
  useInitModule,
  usePlanModule,
  useApplyModule,
  useDestroyModule,
  useCreateVariableMapping,
  useUpdateVariableMapping,
  useCreateVariableMappingTemplate,
} from '@/hooks/useModules';
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
// useModuleLibrary
// ============================================================================

describe('useModuleLibrary', () => {
  it('fetches module library list', async () => {
    const { result } = renderHook(() => useModuleLibrary(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(Array.isArray(result.current.data)).toBe(true);
    expect(result.current.data!.length).toBeGreaterThanOrEqual(1);
    expect(result.current.data![0]).toHaveProperty('name');
    expect(result.current.data![0]).toHaveProperty('category');
  });

  it('returns loading state initially', () => {
    const { result } = renderHook(() => useModuleLibrary(), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);
    expect(result.current.data).toBeUndefined();
  });
});

// ============================================================================
// useModuleDetails
// ============================================================================

describe('useModuleDetails', () => {
  it('fetches module details by id', async () => {
    const { result } = renderHook(() => useModuleDetails(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(result.current.data).toHaveProperty('name', 'vpc');
    expect(result.current.data).toHaveProperty('category', 'network');
  });

  it('is disabled when moduleId is 0', () => {
    const { result } = renderHook(() => useModuleDetails(0), {
      wrapper: createWrapper(),
    });

    // With enabled: false, the query should not fetch
    expect(result.current.isLoading).toBe(false);
    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.data).toBeUndefined();
  });
});

// ============================================================================
// useProjectModules
// ============================================================================

describe('useProjectModules', () => {
  it('fetches project modules', async () => {
    const { result } = renderHook(() => useProjectModules(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(Array.isArray(result.current.data)).toBe(true);
    expect(result.current.data!.length).toBeGreaterThanOrEqual(1);
    expect(result.current.data![0]).toHaveProperty('status');
    expect(result.current.data![0]).toHaveProperty('project_id');
  });

  it('is disabled when projectId is falsy', () => {
    const { result } = renderHook(() => useProjectModules(0), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(false);
    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.data).toBeUndefined();
  });
});

// ============================================================================
// useAddModuleToProject
// ============================================================================

describe('useAddModuleToProject', () => {
  it('mutation calls POST with correct data', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/project-modules/project/:projectId/add', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: 10,
          ...capturedBody,
          status: 'not_initialized',
          created_at: new Date().toISOString(),
        });
      })
    );

    const { result } = renderHook(() => useAddModuleToProject(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      projectId: 1,
      data: { module_library_id: 2, name: 'test-module', variables: { key: 'value' } },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      module_library_id: 2,
      name: 'test-module',
      variables: { key: 'value' },
    });
  });
});

// ============================================================================
// useDeleteModule
// ============================================================================

describe('useDeleteModule', () => {
  it('mutation calls DELETE', async () => {
    let deletedModuleId: string | null = null;
    server.use(
      http.delete('*/api/project-modules/:moduleId', ({ params }) => {
        deletedModuleId = params.moduleId as string;
        return HttpResponse.json({ success: true, message: 'Module removed' });
      })
    );

    const { result } = renderHook(() => useDeleteModule(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(5);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(deletedModuleId).toBe('5');
  });
});

// ============================================================================
// useInitModule
// ============================================================================

describe('useInitModule', () => {
  it('mutation triggers init', async () => {
    const { result } = renderHook(() => useInitModule(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      task_id: 'task-init-001',
      status: 'queued',
    });
  });
});

// ============================================================================
// usePlanModule
// ============================================================================

describe('usePlanModule', () => {
  it('mutation triggers plan', async () => {
    const { result } = renderHook(() => usePlanModule(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      task_id: 'task-plan-001',
      status: 'queued',
    });
  });
});

// ============================================================================
// useApplyModule
// ============================================================================

describe('useApplyModule', () => {
  it('mutation triggers apply with autoApprove', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/project-modules/:moduleId/apply', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: 'task-apply-001', status: 'queued' });
      })
    );

    const { result } = renderHook(() => useApplyModule(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ moduleId: 1, autoApprove: true });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({ auto_approve: true });
    expect(result.current.data).toMatchObject({
      task_id: 'task-apply-001',
      status: 'queued',
    });
  });

  it('handles error state', async () => {
    server.use(
      http.post('*/api/project-modules/:moduleId/apply', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Apply failed' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useApplyModule(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ moduleId: 999, autoApprove: false });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});

// ============================================================================
// useDestroyModule
// ============================================================================

describe('useDestroyModule', () => {
  it('mutation triggers destroy with autoApprove', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/project-modules/:moduleId/destroy', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: 'task-destroy-001', status: 'queued' });
      })
    );

    const { result } = renderHook(() => useDestroyModule(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ moduleId: 3, autoApprove: true });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({ auto_approve: true });
    expect(result.current.data).toMatchObject({
      task_id: 'task-destroy-001',
      status: 'queued',
    });
  });
});

// ============================================================================
// CT-011: Payload Shape Assertions — useUpdateModule
// Backend: UpdateModuleRequest { variable_overrides, deployment_order, enabled }
// ============================================================================

describe('useUpdateModule', () => {
  it('sends correct payload shape matching UpdateModuleRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/project-modules/:moduleId', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 5, project_id: 1, status: 'initialized', ...capturedBody });
      })
    );

    const { result } = renderHook(() => useUpdateModule(), { wrapper: createWrapper() });

    result.current.mutate({
      moduleId: 5,
      data: { variable_overrides: { region: 'us-east-1' }, deployment_order: 2, enabled: true },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Payload matches backend UpdateModuleRequest fields
    expect(capturedBody).toMatchObject({
      variable_overrides: { region: 'us-east-1' },
      deployment_order: 2,
      enabled: true,
    });
    // Not wrapped
    expect(capturedBody).not.toHaveProperty('data');
    expect(capturedBody).not.toHaveProperty('module');
  });
});

// ============================================================================
// CT-012: Payload Shape Assertions — useChangeModuleVersion (D-033)
// Backend: routes/project_modules.py ChangeModuleVersionRequest { target_version }
// Response: ProjectModuleService.change_module_version dict
// ============================================================================

describe('useChangeModuleVersion', () => {
  it('POSTs { target_version } and surfaces the re-pin response', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/project-modules/:moduleId/change-version', async ({ request, params }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        // Real response shape from ProjectModuleService.change_module_version
        return HttpResponse.json({
          success: true,
          message: 'Module re-pinned from version 1.11.4 to 1.20.0. The new version takes effect on the next plan/apply/destroy.',
          module_id: Number(params.moduleId),
          path: 'tools/roksbnkctl',
          previous_version: '1.11.4',
          version: '1.20.0',
          module_library_id: 42,
        });
      })
    );

    const { result } = renderHook(() => useChangeModuleVersion(), { wrapper: createWrapper() });

    result.current.mutate({ moduleId: 5, targetVersion: '1.20.0' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Payload matches backend ChangeModuleVersionRequest exactly
    expect(capturedBody).toEqual({ target_version: '1.20.0' });
    expect(result.current.data?.version).toBe('1.20.0');
    expect(result.current.data?.previous_version).toBe('1.11.4');
  });
});

// ============================================================================
// CT-011: Payload Shape Assertions — useCreateVariableMapping
// Backend: VariableMappingRequest { source_variable_name, target_variable_name, transform_type, is_active, description }
// ============================================================================

describe('useCreateVariableMapping', () => {
  it('sends correct payload shape matching VariableMappingRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/project-modules/:moduleId/variable-mappings', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 1, module_id: 5, ...capturedBody });
      })
    );

    const { result } = renderHook(() => useCreateVariableMapping(), { wrapper: createWrapper() });

    result.current.mutate({
      moduleId: 5,
      data: {
        source_variable_name: 'vpc_id',
        target_variable_name: 'network_id',
        transform_type: 'none',
        is_active: true,
        description: 'Maps VPC ID',
      },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      source_variable_name: 'vpc_id',
      target_variable_name: 'network_id',
      transform_type: 'none',
      is_active: true,
      description: 'Maps VPC ID',
    });
    expect(capturedBody).not.toHaveProperty('mapping');
    expect(capturedBody).not.toHaveProperty('data');
  });
});

// ============================================================================
// CT-011: Payload Shape Assertions — useUpdateVariableMapping
// Backend: VariableMappingRequest (same schema as create)
// ============================================================================

describe('useUpdateVariableMapping', () => {
  it('sends correct payload shape matching VariableMappingRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/project-modules/:moduleId/variable-mappings/:mappingId', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 1, module_id: 5, ...capturedBody });
      })
    );

    const { result } = renderHook(() => useUpdateVariableMapping(), { wrapper: createWrapper() });

    result.current.mutate({
      moduleId: 5,
      mappingId: 1,
      data: {
        source_variable_name: 'vpc_id',
        target_variable_name: 'net_id',
        transform_type: 'uppercase',
        is_active: false,
        description: 'Updated mapping',
      },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      source_variable_name: 'vpc_id',
      target_variable_name: 'net_id',
      transform_type: 'uppercase',
      is_active: false,
    });
  });
});

// ============================================================================
// CT-011: Payload Shape Assertions — useCreateVariableMappingTemplate
// Backend: VariableMappingTemplateRequest { name, description, mappings, is_public }
// ============================================================================

describe('useCreateVariableMappingTemplate', () => {
  it('sends correct payload shape matching VariableMappingTemplateRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/project-modules/variable-mapping-templates', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 1, ...capturedBody });
      })
    );

    const { result } = renderHook(() => useCreateVariableMappingTemplate(), { wrapper: createWrapper() });

    result.current.mutate({
      name: 'VPC Standard Mappings',
      description: 'Standard VPC variable mappings',
      mappings: [{ source: 'vpc_id', target: 'network_id', transform: 'none' }],
      is_public: true,
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      name: 'VPC Standard Mappings',
      description: 'Standard VPC variable mappings',
      mappings: [{ source: 'vpc_id', target: 'network_id', transform: 'none' }],
      is_public: true,
    });
    expect(capturedBody).not.toHaveProperty('template');
    expect(capturedBody).not.toHaveProperty('data');
  });
});
