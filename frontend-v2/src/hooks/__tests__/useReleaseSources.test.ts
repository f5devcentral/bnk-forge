/**
 * Tests for useReleaseSources hooks (ADR-494 CT-012).
 *
 * Response shapes are derived from backend/routes/release_sources.py and
 * backend/schemas/release_source.py:
 *   - ReleaseSourceResponse: id, name, kind, url, has_credential (bool, NO ciphertext),
 *     is_active, auto_sync, sync_interval_hours, last_synced_at, sync_status, sync_error,
 *     release_count, description, created_at, updated_at
 *   - SyncSourceResponse: { source: ReleaseSourceResponse, sync_result: Record<string,number> }
 *   - Sync request body: { manifest_yaml: string } (snake_case, POST /{id}/sync)
 *   - ReleaseSourceTagList: { tags: [{tag, in_catalog, prerelease}], list_error: string|null }
 *   - PullTagsSummary: { added: string[], skipped: string[], failed: [{tag, reason}] }
 *   - Pull request body: { tags: string[] } (POST /{id}/tags:pull)
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useReleaseSources,
  useSyncReleaseSource,
  useReleaseSourceTags,
  usePullReleaseSourceTags,
} from '@/hooks/useReleaseSources';
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

// Real ReleaseSourceResponse shape (mirrors backend/schemas/release_source.py).
// has_credential is a derived bool — the credential ciphertext is never returned.
const mockSource = {
  id: 1,
  name: 'repo.f5.com',
  kind: 'oci',
  url: 'oci://repo.f5.com/release/f5-bigip-k8s-manifest',
  has_credential: false,
  is_active: true,
  auto_sync: false,
  sync_interval_hours: null,
  last_synced_at: null,
  sync_status: 'idle',
  sync_error: null,
  release_count: 0,
  description: null,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

// ============================================================================
// useReleaseSources — list fetch
// ============================================================================

describe('useReleaseSources', () => {
  it('fetches the release sources list with the real ReleaseSourceResponse shape', async () => {
    const { result } = renderHook(() => useReleaseSources(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(Array.isArray(result.current.data)).toBe(true);
    const first = result.current.data![0];

    // Shape assertions — every field from ReleaseSourceResponse must be present
    expect(first.id).toBe(1);
    expect(first.name).toBe('repo.f5.com');
    expect(first.kind).toBe('oci');
    expect(first.has_credential).toBe(false);       // derived bool, never ciphertext
    expect(first.is_active).toBe(true);
    expect(first.sync_status).toBe('idle');
    expect(first.release_count).toBe(0);

    // Credential field must NOT be present (it is never returned by the backend)
    expect(first).not.toHaveProperty('credential');
    expect(first).not.toHaveProperty('credential_encrypted');
  });
});

// ============================================================================
// useSyncReleaseSource — mutation payload shape (CT-012 contract lock)
// ============================================================================

describe('useSyncReleaseSource', () => {
  it('sends manifest_yaml (snake_case) in the POST body to /{id}/sync', async () => {
    // Real SyncSourceResponse shape from backend/routes/release_sources.py:
    //   { source: ReleaseSourceResponse, sync_result: { inserted: N, updated: N, skipped: N } }
    const syncResponse = {
      source: { ...mockSource, sync_status: 'success', release_count: 2 },
      sync_result: { inserted: 2, updated: 0, skipped: 0 },
    };

    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/bare-metal/release-sources/:id/sync', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(syncResponse);
      }),
    );

    const { result } = renderHook(() => useSyncReleaseSource(), {
      wrapper: createWrapper(),
    });

    await act(async () => {
      await result.current.mutateAsync({ id: 1, manifestYaml: 'releases:\n  - version: "2.3.1"\n' });
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // (a) Payload uses snake_case key as the backend route expects
    expect(capturedBody).not.toBeNull();
    expect(capturedBody).toHaveProperty('manifest_yaml');
    expect(capturedBody!['manifest_yaml']).toBe('releases:\n  - version: "2.3.1"\n');

    // (b) camelCase variant must NOT appear — the hook translates manifestYaml → manifest_yaml
    expect(capturedBody).not.toHaveProperty('manifestYaml');

    // (c) Response shape is correct
    expect(result.current.data!.sync_result.inserted).toBe(2);
    expect(result.current.data!.source.sync_status).toBe('success');
  });
});

// ============================================================================
// useReleaseSourceTags — list tags (CT-012: real shape from backend schema)
// ============================================================================

describe('useReleaseSourceTags', () => {
  it('fetches tag list with the real ReleaseSourceTagList shape', async () => {
    // Real ReleaseSourceTagList shape (backend/schemas/release_source.py):
    //   { tags: [{tag, in_catalog, prerelease}], list_error: string|null }
    const tagListResponse = {
      tags: [
        { tag: '2.3.1-3.2598.3-0.0.304', in_catalog: false, prerelease: false },
        { tag: '2.2.1-3.2226.0-0.0.511', in_catalog: true, prerelease: false },
      ],
      list_error: null,
    };

    server.use(
      http.get('*/api/bare-metal/release-sources/:id/tags', () => {
        return HttpResponse.json(tagListResponse);
      }),
    );

    const { result } = renderHook(() => useReleaseSourceTags(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    const data = result.current.data!;
    expect(Array.isArray(data.tags)).toBe(true);
    expect(data.tags).toHaveLength(2);

    // Shape assertions for each tag
    const first = data.tags[0];
    expect(first.tag).toBe('2.3.1-3.2598.3-0.0.304');
    expect(first.in_catalog).toBe(false);
    expect(first.prerelease).toBe(false);

    const second = data.tags[1];
    expect(second.in_catalog).toBe(true);

    // list_error is null on success
    expect(data.list_error).toBeNull();
  });

  it('propagates list_error from a best-effort listing failure', async () => {
    server.use(
      http.get('*/api/bare-metal/release-sources/:id/tags', () => {
        return HttpResponse.json({ tags: [], list_error: 'oras repo tags failed: network error' });
      }),
    );

    const { result } = renderHook(() => useReleaseSourceTags(2), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.tags).toHaveLength(0);
    expect(result.current.data!.list_error).toContain('oras repo tags failed');
  });
});

// ============================================================================
// usePullReleaseSourceTags — pull + upsert (CT-012: request payload + response shape)
// ============================================================================

describe('usePullReleaseSourceTags', () => {
  it('sends tags array in POST body and reads the nested PullTagsSummary response', async () => {
    // Real PullTagsSummary shape (backend/schemas/release_source.py):
    //   { added: string[], skipped: string[], failed: [{tag: string, reason: string}] }
    const pullResponse = {
      added: ['2.3.1-3.2598.3-0.0.304'],
      skipped: [],
      failed: [],
    };

    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/bare-metal/release-sources/:id/tags\\:pull', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(pullResponse);
      }),
    );

    const { result } = renderHook(() => usePullReleaseSourceTags(), {
      wrapper: createWrapper(),
    });

    await act(async () => {
      await result.current.mutateAsync({ id: 1, tags: ['2.3.1-3.2598.3-0.0.304'] });
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // (a) Payload uses the `tags` array key
    expect(capturedBody).not.toBeNull();
    expect(capturedBody).toHaveProperty('tags');
    expect(capturedBody!['tags']).toEqual(['2.3.1-3.2598.3-0.0.304']);

    // (b) Response shape is correct (nested model, not dict)
    const data = result.current.data!;
    expect(Array.isArray(data.added)).toBe(true);
    expect(Array.isArray(data.skipped)).toBe(true);
    expect(Array.isArray(data.failed)).toBe(true);
    expect(data.added).toContain('2.3.1-3.2598.3-0.0.304');
  });

  it('reads failed[].reason from the nested FailedTag model', async () => {
    // Verifies that Pydantic response_model does NOT erase the reason field
    // (would happen if failed were typed as dict — Architect note 8).
    const pullResponse = {
      added: [],
      skipped: [],
      failed: [{ tag: '0.0.1-no-flo', reason: 'missing f5-lifecycle-operator' }],
    };

    server.use(
      http.post('*/api/bare-metal/release-sources/:id/tags\\:pull', () => {
        return HttpResponse.json(pullResponse);
      }),
    );

    const { result } = renderHook(() => usePullReleaseSourceTags(), {
      wrapper: createWrapper(),
    });

    await act(async () => {
      await result.current.mutateAsync({ id: 1, tags: ['0.0.1-no-flo'] });
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    const failedEntry = result.current.data!.failed[0];
    expect(failedEntry.tag).toBe('0.0.1-no-flo');
    expect(failedEntry.reason).toBe('missing f5-lifecycle-operator');
  });
});
