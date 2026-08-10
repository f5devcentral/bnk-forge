/**
 * Tests for useModuleActions hooks (D-034 — module test actions).
 *
 * CT-012: MSW handlers mirror the REAL backend shapes —
 * GET  /api/project-modules/{id}/actions → ModuleActionsListResponse
 *      (backend/schemas/projects.py: module_id, actions[{name,title,description,rating,inputs}], total)
 * POST /api/project-modules/{id}/actions/{name} → ModuleActionSubmitResponse
 *      (success, message, action, task_id, celery_task_id, status) with request
 *      body ModuleActionRequest {inputs} (backend/routes/project_execution.py).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { useModuleActions, useRunModuleAction } from '@/hooks/useModuleActions';
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

// Real response shape: ProjectModuleService.list_module_actions builds each
// action from the pack manifest's actions block; inputs pass through the
// manifest's input defs (name/type/source/default/description/choices).
const mockActionsResponse = {
  module_id: 7,
  actions: [
    {
      name: 'scenario-run',
      title: 'Run scenario',
      description: 'Run one functional scenario against the deployed cluster',
      rating: 'green',
      inputs: [
        {
          name: 'scenario',
          type: 'string',
          source: null,
          default: 'tcpl4lb',
          description: 'Scenario to run',
          choices: ['tcpl4lb', 'udpl4lb', 'ai-inference-e2e'],
        },
      ],
    },
    {
      name: 'ai-scenarios',
      title: 'AI scenarios',
      description: 'needs AI model resources (GPU pool)',
      rating: 'amber',
      inputs: [],
    },
  ],
  total: 2,
};

beforeEach(() => {
  vi.clearAllMocks();
});

// ============================================================================
// useModuleActions (GET)
// ============================================================================

describe('useModuleActions', () => {
  it('fetches the declared actions with rating and input defs', async () => {
    server.use(
      http.get('*/api/project-modules/:moduleId/actions', ({ params }) => {
        expect(params.moduleId).toBe('7');
        return HttpResponse.json(mockActionsResponse);
      })
    );

    const { result } = renderHook(() => useModuleActions(7), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.module_id).toBe(7);
    expect(result.current.data?.total).toBe(2);
    expect(result.current.data?.actions[0].name).toBe('scenario-run');
    expect(result.current.data?.actions[0].rating).toBe('green');
    expect(result.current.data?.actions[0].inputs?.[0].choices).toEqual([
      'tcpl4lb',
      'udpl4lb',
      'ai-inference-e2e',
    ]);
    expect(result.current.data?.actions[1].rating).toBe('amber');
  });

  it('returns an empty list for non-container modules (default handler)', async () => {
    const { result } = renderHook(() => useModuleActions(3), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.actions).toEqual([]);
    expect(result.current.data?.total).toBe(0);
  });

  it('does not fetch when disabled', async () => {
    const spy = vi.fn();
    server.use(
      http.get('*/api/project-modules/:moduleId/actions', () => {
        spy();
        return HttpResponse.json(mockActionsResponse);
      })
    );

    const { result } = renderHook(() => useModuleActions(7, { enabled: false }), {
      wrapper: createWrapper(),
    });

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(spy).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useRunModuleAction (POST)
// ============================================================================

describe('useRunModuleAction', () => {
  it('submits the action and sends the ModuleActionRequest payload', async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post('*/api/project-modules/:moduleId/actions/:actionName', async ({ request, params }) => {
        capturedBody = await request.json();
        expect(params.moduleId).toBe('7');
        expect(params.actionName).toBe('scenario-run');
        // Real submit envelope from ProjectModuleService.submit_action
        return HttpResponse.json({
          success: true,
          message: "Action 'scenario-run' queued",
          action: 'scenario-run',
          task_id: 42,
          celery_task_id: 'ce1e12-abc',
          status: 'queued',
        });
      })
    );

    const { result } = renderHook(() => useRunModuleAction(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      moduleId: 7,
      actionName: 'scenario-run',
      inputs: { scenario: 'tcpl4lb' },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Backend's ModuleActionRequest accepts exactly {inputs: dict | null}
    expect(capturedBody).toEqual({ inputs: { scenario: 'tcpl4lb' } });
    expect(result.current.data?.task_id).toBe(42);
    expect(result.current.data?.status).toBe('queued');
    expect(result.current.data?.success).toBe(true);
  });

  it('sends inputs: null when no inputs are given', async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post('*/api/project-modules/:moduleId/actions/:actionName', async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({
          success: true,
          message: "Action 'ai-scenarios' queued",
          action: 'ai-scenarios',
          task_id: 43,
          celery_task_id: 'ce1e12-def',
          status: 'queued',
        });
      })
    );

    const { result } = renderHook(() => useRunModuleAction(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ moduleId: 7, actionName: 'ai-scenarios' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toEqual({ inputs: null });
  });

  it('surfaces the backend status gate error (module not applied)', async () => {
    server.use(
      http.post('*/api/project-modules/:moduleId/actions/:actionName', () => {
        // handle_route_errors turns BadRequestError into a 400 {detail}
        return HttpResponse.json(
          {
            detail:
              "Cannot run action 'scenario-run': module must be in a post-apply state (applied), current status is 'not_initialized'",
          },
          { status: 400 }
        );
      })
    );

    const { result } = renderHook(() => useRunModuleAction(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ moduleId: 7, actionName: 'scenario-run' });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});
