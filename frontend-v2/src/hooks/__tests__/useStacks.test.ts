/**
 * Tests for useStacks hooks
 *
 * Covers: stackKeys factory, template/preview queries, instance queries,
 * and create/deploy/destroy mutations via MSW handlers.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  stackKeys,
  useStackTemplates,
  useStackTemplate,
  useStackPreview,
  useStackInstances,
  useCreateStackInstance,
  useDeployStack,
  useDestroyStack,
} from '@/hooks/useStacks';
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
// stackKeys
// ============================================================================

describe('stackKeys', () => {
  it('generates correct query keys', () => {
    expect(stackKeys.all).toEqual(['stacks']);
    expect(stackKeys.templates()).toEqual(['stacks', 'templates']);
    expect(stackKeys.template('bnk-full-stack')).toEqual(['stacks', 'templates', 'bnk-full-stack']);
    expect(stackKeys.preview('bnk-full-stack')).toEqual([
      'stacks',
      'templates',
      'bnk-full-stack',
      'preview',
    ]);
    expect(stackKeys.instances(1)).toEqual(['stacks', 'instances', 1]);
    expect(stackKeys.instance(1, 5)).toEqual(['stacks', 'instances', 1, 5]);
    expect(stackKeys.status(1, 5)).toEqual(['stacks', 'instances', 1, 5, 'status']);
  });
});

// ============================================================================
// useStackTemplates
// ============================================================================

describe('useStackTemplates', () => {
  it('fetches stack templates', async () => {
    server.use(
      http.get('*/api/blueprint-catalog/releases', () => HttpResponse.json([])),
    );

    const { result } = renderHook(() => useStackTemplates(), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toHaveLength(2);
    expect(result.current.data![0].slug).toBe('bnk-full-stack');
    expect(result.current.data![1].slug).toBe('minimal-k8s');
  });

  it('maps imported blueprint release category and provider metadata', async () => {
    server.use(
      http.get('*/api/stacks/templates', () => HttpResponse.json([])),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 31,
            blueprint_source_id: 20,
            source_name: 'external-blueprints',
            source_type: 'git',
            blueprint_id: 'ibm-roks-bnk-2-3-ehf.single-nic',
            blueprint_version: '2.3.0-ehf-2-3.2598.3-0.0.18',
            blueprint_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
            blueprint_description: 'Imported blueprint release',
            category: 'infrastructure',
            cloud_provider: 'ibm',
            tags: ['ibm', 'roks', 'infrastructure'],
            schema_version: 1,
            source_path: 'blueprints/bigip-next-roks-single-nic/forge-blueprint.json',
            source_ref: 'refs/heads/main',
            content_sha256: 'a'.repeat(64),
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'imported',
            state_reason: 'manual import',
            is_active: true,
            imported_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: ['imported_at', 'is_active', 'release_state', 'state_reason'],
          },
        ])
      ),
    );

    const { result } = renderHook(() => useStackTemplates(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toHaveLength(1);
    expect(result.current.data![0]).toMatchObject({
      slug: 'release-31',
      category: 'infrastructure',
      cloud_provider: 'ibm',
      tags: ['ibm', 'roks', 'infrastructure'],
      deploy_kind: 'git-release',
    });
  });

  it('deduplicates: builtin release displaces in-code template with matching slug==blueprint_id', async () => {
    // The template 'bnk-full-stack' is in the default mock templates.
    // A builtin release with blueprint_id='bnk-full-stack' should displace it.
    server.use(
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 42,
            blueprint_source_id: 1,
            source_name: 'builtin-blueprints',
            source_type: 'builtin',
            blueprint_id: 'bnk-full-stack',
            blueprint_version: '1.0.0',
            blueprint_name: 'BNK Full Stack (Builtin)',
            blueprint_description: 'Builtin blueprint release',
            category: 'bnk',
            cloud_provider: 'aws',
            tags: [],
            schema_version: 1,
            source_path: null,
            source_ref: null,
            content_sha256: 'b'.repeat(64),
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'imported',
            state_reason: null,
            is_active: true,
            is_featured: false,
            imported_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
        ])
      ),
    );

    const { result } = renderHook(() => useStackTemplates(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Should appear exactly once — the builtin release, not the in-code template
    const entries = result.current.data!.filter(
      (item) => item.slug === 'release-42' || item.slug === 'bnk-full-stack'
    );
    expect(entries).toHaveLength(1);
    expect(entries[0].slug).toBe('release-42');
    expect(entries[0].deploy_kind).toBe('builtin-release');
    // deploy_slug points to the template slug for StackDetailDialog
    expect(entries[0].deploy_slug).toBe('bnk-full-stack');
  });

  it('routes builtin-only releases through imported-release deploy endpoints', async () => {
    server.use(
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 43,
            blueprint_source_id: 1,
            source_name: 'builtin-blueprints',
            source_type: 'builtin',
            blueprint_id: 'awsbnkctl-bnk-demo',
            blueprint_version: '1.0.0',
            blueprint_name: 'AWS BNK Demo (CLI Deploy)',
            blueprint_description: 'CLI deploy blueprint release',
            category: 'infrastructure',
            cloud_provider: 'aws',
            tags: ['awsbnkctl', 'cli'],
            schema_version: 1,
            source_path: 'data/blueprints/awsbnkctl-bnk-demo/forge-blueprint.json',
            source_ref: null,
            content_sha256: 'c'.repeat(64),
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'imported',
            state_reason: null,
            is_active: true,
            is_featured: false,
            imported_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
        ])
      ),
    );

    const { result } = renderHook(() => useStackTemplates(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    const cliRelease = result.current.data!.find((item) => item.blueprint_release_id === 43);
    expect(cliRelease).toMatchObject({
      slug: 'release-43',
      deploy_kind: 'git-release',
    });
    expect(cliRelease?.deploy_slug).toBeUndefined();
  });

  it('D-028: hidden builtin releases do not resurrect their in-code template twin', async () => {
    // Scenario: 12 builtin releases exist, 9 hidden (is_active=false), 3 visible (is_active=true).
    // The default mock returns 2 in-code templates: 'bnk-full-stack' and 'minimal-k8s'.
    // This test uses a fresh template list of 12 slugs to match the 12 releases precisely.
    const allSlugs = Array.from({ length: 12 }, (_, i) => `blueprint-${i + 1}`);

    server.use(
      http.get('*/api/stacks/templates', () =>
        HttpResponse.json(
          allSlugs.map((slug, i) => ({
            id: i + 1,
            name: `Blueprint ${i + 1}`,
            slug,
            category: 'bnk',
            cloud_provider: 'aws',
            difficulty: 'intermediate',
            estimated_time: '10 min',
            is_active: true,
            is_featured: false,
            version: '1.0.0',
            tags: [],
          }))
        )
      ),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json(
          allSlugs.map((slug, i) => ({
            id: 100 + i,
            blueprint_source_id: 1,
            source_name: 'builtin-blueprints',
            source_type: 'builtin',
            blueprint_id: slug,
            blueprint_version: '1.0.0',
            blueprint_name: `Blueprint ${i + 1}`,
            blueprint_description: null,
            category: 'bnk',
            cloud_provider: 'aws',
            tags: [],
            schema_version: 1,
            source_path: null,
            source_ref: null,
            content_sha256: 'a'.repeat(64),
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'imported',
            state_reason: null,
            // Only first 3 are visible; 9 are hidden
            is_active: i < 3,
            is_featured: false,
            imported_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          }))
        )
      ),
    );

    const { result } = renderHook(() => useStackTemplates(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Should show exactly 3 builtin items (the visible releases) — not 12, not 3+9=12 hidden in-code twins
    expect(result.current.data).toHaveLength(3);
    result.current.data!.forEach((item) => {
      expect(item.deploy_kind).toBe('builtin-release');
      expect(item.is_active).toBe(true);
    });

    // None of the 9 hidden blueprint slugs should appear as an in-code template
    const slugsInResult = new Set(result.current.data!.map((item) => item.deploy_slug ?? item.slug));
    for (let i = 3; i < 12; i++) {
      expect(slugsInResult.has(`blueprint-${i + 1}`)).toBe(false);
    }
  });

  it('tags plain templates with deploy_kind=template when no matching builtin release', async () => {
    server.use(
      http.get('*/api/blueprint-catalog/releases', () => HttpResponse.json([])),
    );

    const { result } = renderHook(() => useStackTemplates(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Both default mock templates should be tagged as 'template' with deploy_slug = slug
    result.current.data!.forEach((item) => {
      expect(item.deploy_kind).toBe('template');
      expect(item.deploy_slug).toBe(item.slug);
    });
  });
});

// ============================================================================
// useStackTemplate
// ============================================================================

describe('useStackTemplate', () => {
  it('fetches single template by slug', async () => {
    const { result } = renderHook(() => useStackTemplate('bnk-full-stack'), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      id: 1,
      name: 'BNK Full Stack',
      slug: 'bnk-full-stack',
      category: 'network',
      cloud_provider: 'aws',
    });
  });

  it('is disabled when slug is empty', () => {
    const { result } = renderHook(() => useStackTemplate(''), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.isLoading).toBe(false);
  });
});

// ============================================================================
// useStackPreview
// ============================================================================

describe('useStackPreview', () => {
  it('fetches preview for template', async () => {
    const { result } = renderHook(() => useStackPreview('bnk-full-stack'), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      estimated_time: '15 min',
      estimated_cost: '$150/mo',
    });
    expect(result.current.data!.modules).toHaveLength(2);
  });
});

// ============================================================================
// useStackInstances
// ============================================================================

describe('useStackInstances', () => {
  it('fetches stack instances for project', async () => {
    const { result } = renderHook(() => useStackInstances(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toHaveLength(1);
    expect(result.current.data![0]).toMatchObject({
      id: 1,
      project_id: 1,
      template_id: 1,
      status: 'deployed',
    });
  });

  it('is disabled when projectId is falsy', () => {
    const { result } = renderHook(() => useStackInstances(0), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.isLoading).toBe(false);
  });
});

// ============================================================================
// useCreateStackInstance
// ============================================================================

describe('useCreateStackInstance', () => {
  it('creates a stack instance', async () => {
    const wrapper = createWrapper();
    const { result } = renderHook(() => useCreateStackInstance(1), { wrapper });

    result.current.mutate({
      template_slug: 'bnk-full-stack',
      name: 'my-stack',
    } as never);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      id: 10,
      template_slug: 'bnk-full-stack',
      name: 'my-stack',
      status: 'created',
    });
  });

  it('sends correct payload shape matching StackInstanceCreate', async () => {
    // Backend: StackInstanceCreate { template_id: int, name: str, variables: dict | None }
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/stacks/projects/:projectId/stacks', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 11, ...capturedBody, status: 'created' });
      })
    );

    const wrapper = createWrapper();
    const { result } = renderHook(() => useCreateStackInstance(1), { wrapper });

    result.current.mutate({
      template_id: 5,
      name: 'prod-stack',
      variables: { region: 'us-east-1', vpc_cidr: '10.0.0.0/16' },
    } as never);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      template_id: 5,
      name: 'prod-stack',
      variables: { region: 'us-east-1', vpc_cidr: '10.0.0.0/16' },
    });
    expect(capturedBody).not.toHaveProperty('project_id'); // project_id is in URL
    expect(capturedBody).not.toHaveProperty('stack');
  });
});

// ============================================================================
// useDeployStack
// ============================================================================

describe('useDeployStack', () => {
  it('deploys a stack', async () => {
    const wrapper = createWrapper();
    const { result } = renderHook(() => useDeployStack(1, 1), { wrapper });

    result.current.mutate();

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      status: 'deploying',
      message: 'Stack deployment started',
      current_step: 1,
      total_steps: 4,
    });
  });
});

// ============================================================================
// useDestroyStack
// ============================================================================

describe('useDestroyStack', () => {
  it('destroys a stack', async () => {
    const wrapper = createWrapper();
    const { result } = renderHook(() => useDestroyStack(1, 1), { wrapper });

    result.current.mutate();

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      status: 'destroying',
      message: 'Stack destruction started',
    });
  });

  it('sends force=true query param when stack is stuck in transitional state', async () => {
    // Backend: DELETE /api/stacks/projects/{project_id}/stacks/{stack_id}?force=true
    // The force query param bypasses the in-progress guard for stale deploying/destroying stacks.
    let capturedUrl: string | null = null;
    server.use(
      http.delete('*/api/stacks/projects/:projectId/stacks/:stackId', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({ status: 'deleted', message: 'Stack force-deleted. 0 module(s) may remain orphaned.' });
      })
    );

    const wrapper = createWrapper();
    const { result } = renderHook(() => useDestroyStack(1, 1), { wrapper });

    result.current.mutate({ force: true });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedUrl).toContain('force=true');
    expect(result.current.data).toMatchObject({
      status: 'deleted',
    });
  });

  it('does not send force param when force is falsy', async () => {
    let capturedUrl: string | null = null;
    server.use(
      http.delete('*/api/stacks/projects/:projectId/stacks/:stackId', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({ status: 'destroying', message: 'Stack destruction started' });
      })
    );

    const wrapper = createWrapper();
    const { result } = renderHook(() => useDestroyStack(1, 1), { wrapper });

    result.current.mutate(undefined);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedUrl).not.toContain('force=true');
  });

  it('handles destroy errors', async () => {
    server.use(
      http.delete('*/api/stacks/projects/:projectId/stacks/:stackId', () => {
        return HttpResponse.json(
          { error: { code: 'CONFLICT', message: 'Stack is currently deploying' } },
          { status: 409 }
        );
      })
    );

    const wrapper = createWrapper();
    const { result } = renderHook(() => useDestroyStack(1, 1), { wrapper });

    result.current.mutate();

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});
