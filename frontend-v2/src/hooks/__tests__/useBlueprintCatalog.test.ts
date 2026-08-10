import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { useBlueprintReleases, useBlueprintSources, useImportBlueprintRelease, useDeleteBlueprintRelease, useSetBlueprintReleaseVisibility } from '@/hooks/useBlueprintCatalog';
import { stackKeys } from '@/hooks/useStacks';

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

describe('useBlueprintCatalog hooks', () => {
  it('fetches blueprint sources', async () => {
    const { result } = renderHook(() => useBlueprintSources(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(Array.isArray(result.current.data)).toBe(true);
  });

  it('fetches blueprint releases', async () => {
    const { result } = renderHook(() => useBlueprintReleases(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.[0].blueprint_name).toContain('BIG-IP Next');
  });

  it('imports a blueprint release', async () => {
    const { result } = renderHook(() => useImportBlueprintRelease(), { wrapper: createWrapper() });
    result.current.mutate({ releaseId: 32, stateReason: 'test import' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.release_state).toBe('imported');
  });

  it('deletes a blueprint release', async () => {
    const invalidateSpy = vi
      .spyOn(QueryClient.prototype, 'invalidateQueries')
      .mockResolvedValue(undefined as never);

    const { result } = renderHook(() => useDeleteBlueprintRelease(), { wrapper: createWrapper() });
    result.current.mutate(31);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.success).toBe(true);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: stackKeys.templates() });
    invalidateSpy.mockRestore();
  });

  it('toggles release visibility off via PATCH /visibility', async () => {
    const { result } = renderHook(() => useSetBlueprintReleaseVisibility(), { wrapper: createWrapper() });
    result.current.mutate({ releaseId: 31, isVisible: false });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.is_active).toBe(false);
    expect(result.current.data?.release_state).toBe('imported');
  });

  it('toggles release visibility on via PATCH /visibility', async () => {
    const { result } = renderHook(() => useSetBlueprintReleaseVisibility(), { wrapper: createWrapper() });
    result.current.mutate({ releaseId: 31, isVisible: true });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.is_active).toBe(true);
  });
});
